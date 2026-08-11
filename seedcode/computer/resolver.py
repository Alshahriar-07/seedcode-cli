"""Element resolver: descriptions in, coordinates out.

This is the boundary that keeps coordinates away from the AI. Every semantic UI
action (``ui_click("Sign in button")``, ``ui_type("search box", ...)``) hands
the resolver a *description*; the resolver finds the matching on-screen element
and returns its clickable point. Resolution walks a fixed strategy ladder,
cheapest and most reliable first, and stops at the first confident hit:

1. **Accessibility tree** (UI Automation) — the primary, exact source: fuzzy
   match the description against element role + name.
2. **UIA properties** — a second UIA pass matching automation-id / value /
   help-text for elements a name match missed (unnamed inputs, icon buttons).
3. **DOM inspection** — when the target is a web page and the browser exposes a
   DevTools endpoint, ask the *page* where the element is. Exact, and immune to
   theme/zoom/scroll. Skipped silently when no browser is attached.
4. **OCR** — for windows that expose no automation tree (games, custom-drawn
   apps): locate the text on a screenshot and click its center.
5. **Computer vision** — snap an OCR hit out to the clickable control that
   encloses it, so the click lands on the button body, not the glyphs.
6. **Image matching** — a caller-supplied reference image (an icon/logo with no
   accessibility name), located by normalized-correlation template match.
7. **Relative positioning** — "the field below Username", "the button next to
   Search": resolve the *anchor* by the tiers above, then offset from it.
8. **Absolute coordinates** — last resort, internal only (recovery replays a
   known point); the AI never supplies coordinates.

The AI is never shown, and never supplies, the resulting coordinates. If
nothing resolves, a :class:`ResolveError` flows back so the dispatcher's
recovery engine can retry (refocus, re-snapshot) or, as a last resort, ask the
AI to replan against a fresh semantic snapshot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..ui.fuzzy import fuzzy_match

# A fuzzy score below this means "no confident match" — better to fail and let
# recovery re-snapshot than to click the wrong thing.
_MIN_SCORE = 200.0

# Role words the AI naturally appends to a description ("Save button", "search
# box"); stripped before matching so they don't fight the element's own role.
_ROLE_HINTS = {
    "button": "button",
    "btn": "button",
    "input": "input",
    "field": "input",
    "box": "input",
    "textbox": "input",
    "link": "link",
    "menu": "menu",
    "menuitem": "menuitem",
    "item": "listitem",
    "tab": "tab",
    "checkbox": "checkbox",
    "check": "checkbox",
    "radio": "radio",
    "combobox": "combobox",
    "dropdown": "combobox",
    "icon": "button",
}

# Direction phrases for the relative-positioning tier, mapped to a unit vector
# in screen space (x right, y down). Longest phrases first so "to the right of"
# is matched before "right".
_DIRECTIONS: list[tuple[str, tuple[int, int]]] = [
    ("to the right of", (1, 0)),
    ("to the left of", (-1, 0)),
    ("right of", (1, 0)),
    ("left of", (-1, 0)),
    ("underneath", (0, 1)),
    ("beneath", (0, 1)),
    ("below", (0, 1)),
    ("under", (0, 1)),
    ("above", (0, -1)),
    ("over", (0, -1)),
    ("next to", (1, 0)),
    ("beside", (1, 0)),
    ("near", (0, 0)),
]


class ResolveError(Exception):
    """A described element could not be located on screen."""


@dataclass(slots=True)
class ResolvedElement:
    """An element the resolver located, with the point to act on."""

    x: int
    y: int
    role: str
    name: str
    score: float
    # Which ladder tier produced the hit: accessibility | uia | dom | ocr | cv |
    # image | relative | absolute.
    source: str
    # The ordered list of tiers attempted before this one succeeded (diagnostic
    # trail, surfaced in logs and to recovery).
    tried: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f'{self.role} "{self.name or "(unnamed)"}" via {self.source}'


class ElementResolver:
    """Turns element descriptions into concrete screen points.

    Deterministic and offline. Drivers are injectable so the resolution logic
    can be unit-tested without a live desktop.
    """

    def __init__(self, vision: Any = None, screen: Any = None, dom: Any = None) -> None:
        if vision is None:
            from . import vision as vision  # type: ignore
        if screen is None:
            from . import screen as screen  # type: ignore
        if dom is None:
            from . import browser_cdp as dom  # type: ignore
        self._vision = vision
        self._screen = screen
        # The DOM tier's provider. Injectable so resolution can be tested
        # without a live browser, and so a caller can disable the tier by
        # passing a stub.
        self._dom = dom

    def resolve(
        self,
        description: str,
        window_title: str | None = None,
        *,
        template: str | None = None,
    ) -> ResolvedElement:
        """Locate the element matching ``description`` and return its point.

        ``template`` optionally points at a reference image for the
        image-matching tier (skills supply it; the AI never does).
        """
        desc = (description or "").strip()
        if not desc:
            raise ResolveError("No element description was given.")

        tried: list[str] = []

        # Snapshot the accessibility tree once; tiers 1–2 reuse it.
        try:
            _title, elements = self._vision.snapshot(window_title)
        except Exception:
            elements = []

        # A spatial phrase ("the field below Username") is an explicit
        # instruction, not a fallback: honour it before fuzzy matching, which
        # would otherwise match the *anchor* itself and click the wrong control.
        # The anchor is still located via the accessibility tiers below, so
        # mechanism preference is preserved.
        anchor_desc, vector = self._split_relative(desc)
        if anchor_desc is not None:
            tried.append("relative")
            rel = self._resolve_relative(anchor_desc, vector, elements, window_title, tried)
            if rel is not None:
                return rel

        # 1) Accessibility — exact, preferred.
        tried.append("accessibility")
        hit = self._match_elements(desc, elements)
        if hit is not None:
            return self._tag(hit, tried)

        # 2) UIA properties — automation-id / value / help-text.
        tried.append("uia")
        hit = self._match_uia_properties(desc, elements)
        if hit is not None:
            return self._tag(hit, tried)

        # 3) DOM — ask the page itself, when one is attached.
        tried.append("dom")
        dom_hit = self._locate_dom(desc)
        if dom_hit is not None:
            return self._tag(dom_hit, tried)

        # 4) OCR — text on a screenshot (+ 5) CV refinement of the hit box).
        tried.append("ocr")
        ocr_box = self._locate_ocr(desc, window_title)
        if ocr_box is not None:
            tried.append("cv")
            box, source = self._refine_with_cv(ocr_box, window_title)
            left, top, width, height = box
            return ResolvedElement(
                x=int(left + width // 2), y=int(top + height // 2),
                role="text", name=desc, score=_MIN_SCORE, source=source, tried=list(tried),
            )

        # 6) Image matching — a caller-supplied reference image.
        if template:
            tried.append("image")
            img_hit = self._match_template(template, window_title)
            if img_hit is not None:
                return self._tag(img_hit, tried)

        raise ResolveError(
            f'Could not find "{desc}" on screen (tried: {", ".join(tried)}). '
            "Take a fresh computer_see snapshot and describe a visible element."
        )

    def resolve_point(self, x: int, y: int) -> ResolvedElement:
        """Absolute-coordinate tier — internal last resort (never AI-driven)."""
        return ResolvedElement(
            x=int(x), y=int(y), role="point", name=f"({x}, {y})",
            score=_MIN_SCORE, source="absolute", tried=["absolute"],
        )

    # --- accessibility matching ---------------------------------------------
    def _match_elements(self, desc: str, elements: list) -> ResolvedElement | None:
        if not elements:
            return None
        query, wanted_role = self._split_role_hint(desc)
        best: ResolvedElement | None = None
        for el in elements:
            # Score the query against the element name (primary) and, more
            # weakly, its role, so "search box" still finds an unnamed input.
            name_score = fuzzy_match(query, el.name).score if query else 0.0
            role_score = 0.0
            if wanted_role and el.role == wanted_role:
                role_score = 150.0
            elif wanted_role and el.role != wanted_role:
                role_score = -60.0  # penalise a role mismatch, don't exclude
            score = name_score + role_score
            # An unnamed element that matches only by role still counts when
            # the description was essentially just a role ("the input").
            if not query and wanted_role and el.role == wanted_role:
                score = _MIN_SCORE + 1.0
            if not getattr(el, "enabled", True):
                score -= 80.0
            if best is None or score > best.score:
                best = ResolvedElement(
                    x=el.x, y=el.y, role=el.role, name=el.name,
                    score=score, source="accessibility",
                )
        if best is not None and best.score >= _MIN_SCORE:
            return best
        return None

    def _match_uia_properties(self, desc: str, elements: list) -> ResolvedElement | None:
        """Second UIA pass: match automation-id / value / help-text.

        Buttons drawn with only an icon, and inputs whose accessible name is
        empty, often still carry an AutomationId ("searchBox"), a Value, or
        help text. Name-based matching misses those; this catches them.
        Defensive: elements without these attributes (test fakes, minimal
        drivers) simply contribute nothing.
        """
        query, _role = self._split_role_hint(desc)
        needle = " ".join(query.lower().split())
        if not needle:
            return None
        best: ResolvedElement | None = None
        for el in elements:
            haystacks = [
                str(getattr(el, "automation_id", "") or ""),
                str(getattr(el, "value", "") or ""),
                str(getattr(el, "help_text", "") or ""),
            ]
            score = 0.0
            for hay in haystacks:
                if not hay:
                    continue
                hay_norm = " ".join(hay.lower().split())
                if needle in hay_norm or hay_norm in needle:
                    score = max(score, _MIN_SCORE + 20.0)
                else:
                    score = max(score, fuzzy_match(query, hay).score)
            if not getattr(el, "enabled", True):
                score -= 80.0
            if score >= _MIN_SCORE and (best is None or score > best.score):
                best = ResolvedElement(
                    x=el.x, y=el.y, role=getattr(el, "role", "element"),
                    name=getattr(el, "name", "") or needle, score=score, source="uia",
                )
        return best

    def _split_role_hint(self, desc: str) -> tuple[str, str | None]:
        """Peel a trailing role word off the description, if present."""
        words = desc.split()
        if len(words) >= 2:
            role = _ROLE_HINTS.get(words[-1].lower())
            if role is not None:
                return " ".join(words[:-1]).strip(), role
        # A one-word role-only description ("button") still carries the hint.
        if len(words) == 1:
            role = _ROLE_HINTS.get(words[0].lower())
            if role is not None:
                return "", role
        return desc, None

    # --- DOM -----------------------------------------------------------------
    def _locate_dom(self, desc: str) -> ResolvedElement | None:
        """Ask the attached web page where the described element is.

        Returns None — cheaply and silently — whenever no browser is attached,
        which is the common case for native-app automation. The page reports
        screen coordinates directly, so no conversion is needed here.
        """
        dom = self._dom
        if dom is None:
            return None
        try:
            if not dom.is_available():
                return None
            box = dom.locate_text(self._strip_role(desc))
        except Exception:
            return None
        if box is None:
            return None
        return ResolvedElement(
            x=int(box.x), y=int(box.y), role="dom",
            name=getattr(box, "text", "") or desc,
            score=_MIN_SCORE, source="dom",
        )

    def _strip_role(self, desc: str) -> str:
        """Drop a trailing role word — the DOM matches on visible text."""
        query, _role = self._split_role_hint(desc)
        return query or desc

    # --- OCR + CV ------------------------------------------------------------
    def _locate_ocr(self, desc: str, window_title: str | None) -> tuple[int, int, int, int] | None:
        """Find text on a screenshot; return its (left, top, width, height)."""
        locate = getattr(self._vision, "ocr_locate", None)
        if locate is None or self._screen is None or not self._vision.ocr_available():
            return None
        try:
            path = self._screen.capture()
            return locate(path, desc)  # (left, top, width, height) or None
        except Exception:
            return None

    def _refine_with_cv(
        self, box: tuple[int, int, int, int], window_title: str | None
    ) -> tuple[tuple[int, int, int, int], str]:
        """Snap an OCR box to its enclosing control via CV; report the tier."""
        refine = getattr(self._vision, "refine_region_cv", None)
        if refine is None or self._screen is None or not getattr(self._vision, "cv_available", lambda: False)():
            return box, "ocr"
        try:
            path = self._screen.capture()
            refined = refine(path, box)
            return (refined, "cv") if refined != box else (box, "ocr")
        except Exception:
            return box, "ocr"

    # --- image matching ------------------------------------------------------
    def _match_template(self, template: str, window_title: str | None) -> ResolvedElement | None:
        locate = getattr(self._vision, "template_locate", None)
        if locate is None or self._screen is None:
            return None
        try:
            path = self._screen.capture()
            box = locate(path, template)
        except Exception:
            return None
        if not box:
            return None
        left, top, width, height = box
        return ResolvedElement(
            x=int(left + width // 2), y=int(top + height // 2),
            role="image", name=str(template), score=_MIN_SCORE, source="image",
        )

    # --- relative positioning ------------------------------------------------
    def _split_relative(self, desc: str) -> tuple[str | None, tuple[int, int]]:
        """Split "<target> <direction> <anchor>" into (anchor, unit vector).

        Returns (None, (0, 0)) when the description carries no spatial phrase.
        """
        low = desc.lower()
        for phrase, vector in _DIRECTIONS:
            m = re.search(rf"\b{re.escape(phrase)}\b", low)
            if m:
                anchor = desc[m.end():].strip(" .,:") or desc[: m.start()].strip(" .,:")
                if anchor:
                    return anchor, vector
        return None, (0, 0)

    def _resolve_relative(
        self,
        anchor_desc: str,
        vector: tuple[int, int],
        elements: list,
        window_title: str | None,
        tried: list[str],
    ) -> ResolvedElement | None:
        """Resolve the anchor element, then offset by the direction vector."""
        anchor = self._match_elements(anchor_desc, elements)
        if anchor is None:
            anchor = self._match_uia_properties(anchor_desc, elements)
        if anchor is None:
            return None
        # Offset by roughly the element's own extent so we land on the adjacent
        # control, not still inside the anchor. Fall back to a sensible step.
        el = self._element_named(elements, anchor.name)
        step_x = (getattr(el, "width", 0) or 120) if vector[0] else 0
        step_y = (getattr(el, "height", 0) or 32) if vector[1] else 0
        # A little breathing room past the edge of the anchor.
        gap_x = int(vector[0] * (step_x // 2 + 16))
        gap_y = int(vector[1] * (step_y // 2 + 16))
        return ResolvedElement(
            x=anchor.x + gap_x, y=anchor.y + gap_y,
            role="relative", name=f"{_vec_name(vector)} {anchor.name}".strip(),
            score=_MIN_SCORE, source="relative", tried=list(tried),
        )

    @staticmethod
    def _element_named(elements: list, name: str):
        for el in elements:
            if getattr(el, "name", None) == name:
                return el
        return None

    @staticmethod
    def _tag(hit: ResolvedElement, tried: list[str]) -> ResolvedElement:
        hit.tried = list(tried)
        return hit


def _vec_name(vector: tuple[int, int]) -> str:
    return {
        (1, 0): "right of", (-1, 0): "left of",
        (0, 1): "below", (0, -1): "above", (0, 0): "near",
    }.get(vector, "near")
