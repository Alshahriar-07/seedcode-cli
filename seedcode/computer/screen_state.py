"""Screen Intelligence Engine: structured desktop state with semantic IDs.

The engine answers "what is on screen" as *structured JSON*, not pixels:
UI Automation is the primary source (via the existing :mod:`.vision`
walker), window metadata secondary, OCR/screenshot only ever a fallback
owned elsewhere. The AI never receives — and never supplies — raw
coordinates for normal interaction; it reasons over stable element ids:

    find_ui_element("Play")  ->  {"id": "element_042", "role": "button", ...}
    click("element_042")     ->  engine resolves the id, re-validates
                                 freshness against the live tree, then acts

Design points the tests pin:

* **Semantic ids are session-stable.** ``element_042`` keeps pointing at the
  same element across queries as long as the underlying snapshot is valid;
  ids are assigned deterministically per snapshot generation.
* **Freshness validation.** Acting on a cached element re-reads the live
  tree and re-locates the element by (role, name, automation_id). If it is
  gone or moved, :class:`StaleElementError` is raised — never a blind click
  at an old coordinate.
* **Caching with change hashes.** ``window_hash`` / ``element_hash`` detect
  change; unchanged desktops reuse cached state instead of rescanning.
  Scans are FULL, INCREMENTAL (windows only), or TARGETED (one element).
* **Targeted queries.** ``find_ui_element`` returns the match (with nearby
  context), not the whole tree.

All drivers are injectable, so the engine is fully unit-testable without a
desktop. Windows-only at runtime; importing is safe everywhere.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import ElementNotFoundError, ScreenUnavailableError, StaleElementError
from ..core.limits import MAX_WAIT_ELEMENT_S, MAX_WAIT_POLL_S, clamp_wait


# --- models ---------------------------------------------------------------------

@dataclass(slots=True)
class Element:
    """One UI element with a stable semantic id and full metadata."""

    id: str
    role: str
    name: str
    x: int                    # center, physical screen space
    y: int
    width: int
    height: int
    enabled: bool = True
    value: str = ""
    automation_id: str = ""
    control_type: str = ""    # raw UIA type ("ButtonControl"), when known
    # State flags where the source exposes them.
    focused: bool = False
    selected: bool = False
    checked: bool = False
    # Element ids are only guaranteed fresh within the snapshot generation
    # they were issued in; actions re-validate.
    generation: int = 0

    @property
    def center(self) -> tuple[int, int]:
        return (self.x, self.y)

    def bounds(self) -> list[int]:
        return [self.x - self.width // 2, self.y - self.height // 2,
                self.width, self.height]

    def describe(self) -> str:
        state = "" if self.enabled else " (disabled)"
        return f'{self.role} "{self.name or "(unnamed)"}"{state}'


@dataclass(slots=True)
class WindowRef:
    """One top-level window in engine form."""

    id: str
    title: str
    pid: int = 0
    focused: bool = False
    minimized: bool = False
    bounds: list[int] = field(default_factory=lambda: [0, 0, 0, 0])  # l,t,w,h

    def describe(self) -> str:
        mark = " [focused]" if self.focused else (" [minimized]" if self.minimized else "")
        return f'"{self.title}"{mark} at ({self.bounds[0]}, {self.bounds[1]}) size {self.bounds[2]}x{self.bounds[3]}'


@dataclass(slots=True)
class ScreenSnapshot:
    """A generation of screen state (windows + elements) plus change hashes."""

    generation: int
    created_at: float
    windows: list[WindowRef] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    active_window: WindowRef | None = None
    window_hash: str = ""
    element_hash: str = ""

    def window_by_title(self, fragment: str) -> WindowRef | None:
        low = (fragment or "").strip().lower()
        for w in self.windows:
            if low and low in w.title.lower():
                return w
        return None


# --- the engine -------------------------------------------------------------------

class ScreenEngine:
    """Structured screen state: cache, semantic ids, targeted queries.

    ``vision`` and ``windows`` are the existing drivers (injectable for
    tests). A generation counter invalidates ids when the desktop changes;
    the cache avoids rescanning an unchanged desktop.
    """

    def __init__(self, vision: Any = None, windows: Any = None,
                 max_elements: int = 150) -> None:
        if vision is None:
            from . import vision as vision  # type: ignore
        if windows is None:
            from . import windows as windows  # type: ignore
        self._vision = vision
        self._windows = windows
        self._max_elements = max_elements
        self._generation = 0
        self._cache: ScreenSnapshot | None = None
        # element_id -> Element for the current generation.
        self._by_id: dict[str, Element] = {}

    # --- availability -----------------------------------------------------------
    def _read_windows(self) -> tuple[list[WindowRef], WindowRef | None]:
        """Best-effort window enumeration; empty on failure (no desktop)."""
        try:
            raw = self._windows.list_windows()
        except Exception:
            return [], None
        refs: list[WindowRef] = []
        for i, w in enumerate(raw, start=1):
            refs.append(
                WindowRef(
                    id=f"window_{i:03d}",
                    title=getattr(w, "title", "") or "",
                    pid=int(getattr(w, "pid", 0) or 0),
                    focused=bool(getattr(w, "active", False)),
                    minimized=bool(getattr(w, "minimized", False)),
                    bounds=[int(getattr(w, "left", 0)), int(getattr(w, "top", 0)),
                            int(getattr(w, "width", 0)), int(getattr(w, "height", 0))],
                )
            )
        active = next((r for r in refs if r.focused), None)
        return refs, active

    def _read_elements(self, window_title: str | None) -> list[tuple[Any, ...]]:
        """Raw element tuples from the vision driver ([] when unavailable)."""
        try:
            _title, elements = self._vision.snapshot(window_title)
        except Exception:
            return []
        return [(e, getattr(e, "role", ""), getattr(e, "name", ""),
                 getattr(e, "x", 0), getattr(e, "y", 0),
                 getattr(e, "width", 0), getattr(e, "height", 0),
                 bool(getattr(e, "enabled", True)),
                 getattr(e, "automation_id", "") or "",
                 getattr(e, "value", "") or "") for e in (elements or [])]

    # --- hashing ---------------------------------------------------------------
    @staticmethod
    def _hash_parts(parts: list[str]) -> str:
        import hashlib

        return hashlib.sha1("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()[:16]

    def _window_hash(self, wins: list[WindowRef]) -> str:
        return self._hash_parts(
            [f"{w.title}|{w.bounds}|{w.focused}|{w.minimized}" for w in wins]
        )

    def _element_hash(self, raw: list[tuple[Any, ...]]) -> str:
        return self._hash_parts(
            [f"{r[1]}|{r[2]}|{r[3]}|{r[4]}|{r[7]}" for r in raw]
        )

    # --- scans -------------------------------------------------------------------
    def refresh(self, mode: str = "full", window_title: str | None = None) -> ScreenSnapshot:
        """Re-read screen state.

        Modes: ``full`` (windows + elements), ``incremental`` (windows only —
        cached elements kept), ``targeted`` (validate one element; see
        :meth:`locate`).
        """
        mode = (mode or "full").strip().lower()
        wins, active = self._read_windows()
        if mode == "incremental" and self._cache is not None:
            w_hash = self._window_hash(wins)
            if w_hash == self._cache.window_hash:
                return self._cache  # nothing changed at window level
            self._generation += 1
            snap = ScreenSnapshot(
                generation=self._generation, created_at=time.time(),
                windows=wins, active_window=active,
                window_hash=w_hash,
                element_hash=self._cache.element_hash,
            )
            self._cache = snap
            return snap

        raw = self._read_elements(window_title)
        w_hash = self._window_hash(wins)
        e_hash = self._element_hash(raw)
        if (
            mode != "targeted"
            and self._cache is not None
            and self._cache.window_hash == w_hash
            and self._cache.element_hash == e_hash
        ):
            # Identical desktop: keep the same generation (ids stay valid).
            self._cache.created_at = time.time()
            return self._cache

        self._generation += 1
        snap = ScreenSnapshot(
            generation=self._generation, created_at=time.time(),
            windows=wins, active_window=active,
            window_hash=w_hash, element_hash=e_hash,
        )
        self._by_id = {}
        for i, r in enumerate(raw[: self._max_elements], start=1):
            el = Element(
                id=f"element_{i:03d}",
                role=str(r[1] or "element"),
                name=str(r[2] or ""),
                x=int(r[3]), y=int(r[4]), width=int(r[5]), height=int(r[6]),
                enabled=bool(r[7]), automation_id=str(r[8]), value=str(r[9]),
                control_type=str(getattr(r[0], "control_type", "") or getattr(r[0], "_control_type", "") or ""),
                generation=self._generation,
            )
            snap.elements.append(el)
            self._by_id[el.id] = el
        self._cache = snap
        return snap

    # --- targeted queries --------------------------------------------------------
    def _match_score(self, query: str, el: Element) -> float:
        """Cheap containment/role scoring (fuzzy matching lives in the resolver)."""
        q = (query or "").strip().lower()
        if not q:
            return 0.0
        name = (el.name or "").lower()
        aid = (el.automation_id or "").lower()
        score = 0.0
        if q in name:
            score = 300.0
        elif name and name in q:
            score = 200.0
        elif q in aid:
            score = 280.0
        # A trailing role hint ("play button") is a plus, not a requirement.
        if any(hint in q for hint in (el.role, "button" if el.role == "button" else el.role)):
            score += 20.0
        if not el.enabled:
            score -= 80.0
        return score

    def find_element(self, query: str, *, window: str | None = None,
                     fresh: bool = True) -> Element:
        """Targeted query: one element by name/role/automation-id.

        ``fresh=True`` (the default) always re-reads the live tree so ids are
        minted against current reality — the AI cannot act on a stale picture.
        Raises :class:`ElementNotFoundError` when nothing matches.
        """
        if fresh:
            snap = self.refresh(mode="full", window_title=window)
        else:
            snap = self._cache or self.refresh(mode="full", window_title=window)
        best: tuple[float, Element] | None = None
        for el in snap.elements:
            s = self._match_score(query, el)
            if s > 0 and (best is None or s > best[0]):
                best = (s, el)
        if best is None or best[0] <= 0:
            raise ElementNotFoundError(
                f'No element matching "{query}" on screen. '
                "Try a different description or check the window is open."
            )
        return best[1]

    def get_element(self, element_id: str) -> Element:
        """Fetch a cached element by id, validating freshness.

        The id must exist in the cached generation AND still resolve in a
        fresh scan by (role, name, automation_id) — otherwise the UI changed
        and :class:`StaleElementError` tells the planner to re-query.
        """
        el = self._by_id.get((element_id or "").strip().lower())
        if el is None:
            if self._by_id:
                raise StaleElementError(element_id or "")
            # Cold cache (id quoted from a previous process/query): a scan is
            # required before anything can be declared stale.
            self.refresh(mode="full")
            el = self._by_id.get((element_id or "").strip().lower())
            if el is None:
                raise StaleElementError(element_id or "")
        # Freshness: re-read and re-locate by identity triple.
        snap = self.refresh(mode="full")
        for candidate in snap.elements:
            if (
                candidate.role == el.role
                and candidate.name == el.name
                and candidate.automation_id == el.automation_id
            ):
                # Live position wins: return the *fresh* element, keep the id
                # stable so the caller's reference remains meaningful.
                fresh = Element(
                    id=el.id, role=candidate.role, name=candidate.name,
                    x=candidate.x, y=candidate.y, width=candidate.width,
                    height=candidate.height, enabled=candidate.enabled,
                    value=candidate.value, automation_id=candidate.automation_id,
                    control_type=candidate.control_type,
                    focused=candidate.focused, selected=candidate.selected,
                    checked=candidate.checked, generation=snap.generation,
                )
                self._by_id[el.id] = fresh
                return fresh
        raise StaleElementError(el.id)

    def active_window(self) -> WindowRef | None:
        snap = self.refresh(mode="incremental")
        return snap.active_window

    def list_windows(self) -> list[WindowRef]:
        return self.refresh(mode="incremental").windows

    # --- serialization -------------------------------------------------------------
    def state_json(self, include_elements: bool = True) -> dict[str, Any]:
        """The model-facing structured state (compact, JSON-safe)."""
        snap = self.refresh(mode="incremental")
        state: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(snap.created_at)),
            "generation": snap.generation,
            "active_window": (
                {"id": snap.active_window.id, "app": _app_from_title(snap.active_window.title),
                 "title": snap.active_window.title, "pid": snap.active_window.pid,
                 "bounds": snap.active_window.bounds}
                if snap.active_window else None
            ),
            "windows": [
                {"id": w.id, "app": _app_from_title(w.title), "title": w.title,
                 "pid": w.pid, "visible": not w.minimized, "focused": w.focused,
                 "bounds": w.bounds}
                for w in snap.windows
            ],
        }
        if include_elements:
            state["elements"] = [
                {"id": e.id, "role": e.role, "name": e.name,
                 "bounds": e.bounds(), "center": list(e.center),
                 "enabled": e.enabled, "automation_id": e.automation_id or None}
                for e in snap.elements
            ]
        return state

    def wait_for_element(self, query: str, *, timeout_s: float = MAX_WAIT_ELEMENT_S,
                         window: str | None = None) -> Element:
        """State-based wait (no blind sleep): poll until the element appears."""
        deadline = time.monotonic() + clamp_wait(timeout_s, 30.0)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self.find_element(query, window=window, fresh=True)
            except ElementNotFoundError as exc:
                last_error = exc
                time.sleep(MAX_WAIT_POLL_S)
        raise last_error or ElementNotFoundError(f'"{query}" never appeared.')


def _app_from_title(title: str) -> str:
    """Best-effort app name from a window title ('Document — App' convention)."""
    for sep in (" — ", " - ", " – "):
        if sep in title:
            return title.rsplit(sep, 1)[-1].strip() or title.strip()
    return title.strip()


# --- shared session engine ------------------------------------------------------------
_ENGINE: ScreenEngine | None = None


def get_screen_engine(vision: Any = None, windows: Any = None) -> ScreenEngine:
    """Process-wide engine (built on first use; injectable for tests)."""
    global _ENGINE
    if _ENGINE is None or vision is not None or windows is not None:
        if _ENGINE is None:
            _ENGINE = ScreenEngine(vision=vision, windows=windows)
    return _ENGINE


def reset_screen_engine() -> None:
    """Drop the cached engine (tests, session teardown)."""
    global _ENGINE
    _ENGINE = None
