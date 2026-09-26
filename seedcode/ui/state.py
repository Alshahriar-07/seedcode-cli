"""Centralized reactive application state (v8.2.5).

The persistent TUI must never rebuild the world to show a new value: the header,
the content region and the composer are rendered from **one** object, and a
change to that object repaints only what changed. That object is
:class:`AppState`.

Design rules (they mirror the release contract):

* every value is read from real application state — provider, model, mode,
  status, workspace, masked key, context budget, connection state, activity;
  nothing is synthesised for display and an unknown value stays empty rather
  than being filled with a plausible-looking placeholder;
* mutation is thread-safe. The TUI runs its turn on a worker thread while the
  prompt_toolkit application owns the main thread, so ``update`` is guarded by a
  re-entrant lock and listeners are notified *outside* the lock;
* listeners are best-effort. A subscriber that raises (a repaint on a terminal
  that just disappeared, a test double) must never break the session, so
  notification swallows and logs nothing that could stop the work.

``version`` is a monotonically increasing counter. Renderers can use it to skip
work when nothing changed — the "only affected components update" rule.
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "Activity",
    "AppState",
    "STATUS_LABELS",
    "STATUS_MARKS",
    "STATUS_ROLES",
    "Status",
    "status_fragment",
    "status_role",
]


class Status(str, enum.Enum):
    """The live session status shown in the header.

    The values are the real phases of a turn, not decoration: ``READY`` waits
    for the user, ``THINKING`` is the pre-first-token wait, ``WORKING`` is an
    agent turn in progress, ``EXECUTING`` is a running tool/command,
    ``COMPLETED`` is a finished agent task, ``ERROR`` is a failed operation and
    ``CANCELLED`` a user interrupt.
    """

    READY = "ready"
    THINKING = "thinking"
    WORKING = "working"
    EXECUTING = "executing"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


#: The short label rendered next to the mark.
STATUS_LABELS: dict[Status, str] = {
    Status.READY: "Ready",
    Status.THINKING: "Thinking",
    Status.WORKING: "Working",
    Status.EXECUTING: "Running",
    Status.WAITING: "Waiting",
    Status.COMPLETED: "Completed",
    Status.ERROR: "Error",
    Status.CANCELLED: "Cancelled",
}

#: The glyph for each status (Unicode; the header falls back to ASCII itself).
STATUS_MARKS: dict[Status, str] = {
    Status.READY: "\u25cf",       # ●
    Status.THINKING: "\u25cc",    # ◌
    Status.WORKING: "\u25cc",     # ◌
    Status.EXECUTING: "\u25cf",   # ●
    Status.WAITING: "\u25cc",     # ◌
    Status.COMPLETED: "\u2713",   # ✓
    Status.ERROR: "\u26a0",       # ⚠
    Status.CANCELLED: "\u25a0",   # ■
}

#: ASCII fallbacks for consoles that cannot draw the glyphs.
STATUS_MARKS_ASCII: dict[Status, str] = {
    Status.READY: "*",
    Status.THINKING: "o",
    Status.WORKING: "o",
    Status.EXECUTING: "*",
    Status.WAITING: "?",
    Status.COMPLETED: "+",
    Status.ERROR: "!",
    Status.CANCELLED: "=",
}

#: Palette role used to colour each status.
STATUS_ROLES: dict[Status, str] = {
    Status.READY: "success",
    Status.THINKING: "accent",
    Status.WORKING: "accent",
    Status.EXECUTING: "accent",
    Status.WAITING: "warning",
    Status.COMPLETED: "success",
    Status.ERROR: "error",
    Status.CANCELLED: "warning",
}


def status_role(status: Status) -> str:
    """The palette role for ``status`` (never raises on an unknown value)."""
    try:
        return STATUS_ROLES[Status(status)]
    except (ValueError, KeyError):
        return "text"


def _role_style(role: str, *, bold: bool = False) -> str:
    """An inline prompt_toolkit style for a palette role.

    Inline style strings (``fg:#2ecc71 bold``) keep the header themed without
    registering style classes, so a theme switch is picked up on the next frame
    with no re-registration step.
    """
    from .theme import active_palette

    palette = active_palette()
    color = getattr(palette, role, palette.text)
    return f"bold fg:{color}" if bold else f"fg:{color}"


def status_fragment(status: Status, *, legacy: bool = False) -> tuple[str, str]:
    """``(style, "◉ Working")`` for the header's status cell."""
    marks = STATUS_MARKS_ASCII if legacy else STATUS_MARKS
    mark = marks.get(status, "?")
    return _role_style(status_role(status)), f"{mark} {STATUS_LABELS.get(status, str(status))}"


@dataclass
class Activity:
    """One live activity line in the content stream.

    ``kind`` selects the marker/style (``action``, ``success``, ``error``,
    ``note``) and ``text`` is what the engine actually reported — the TUI never
    invents an activity and never shows one before the operation starts.
    """

    kind: str
    text: str


@dataclass
class AppState:
    """The single reactive source of truth for the persistent UI."""

    provider: str = ""
    model: str = ""
    mode: str = "Chat"
    status: Status = Status.READY
    workspace: str = ""
    api_key: str = ""  # always the masked form; the real key never enters here
    key_required: bool = False
    context_limit: int = 0
    connection: str = ""
    operation: str = ""
    agent_state: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)

    # --- reactivity ---------------------------------------------------------
    version: int = 0
    _listeners: list[Callable[["AppState"], None]] = field(
        default_factory=list, repr=False, compare=False
    )
    _lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False, compare=False
    )

    # --- subscription -------------------------------------------------------
    def subscribe(self, listener: Callable[["AppState"], None]) -> None:
        """Register ``listener``; it is called once immediately, then on change."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)
        self._notify_single(listener)

    def unsubscribe(self, listener: Callable[["AppState"], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def _notify_single(self, listener: Callable[["AppState"], None]) -> None:
        try:
            listener(self)
        except Exception:
            pass  # a subscriber must never break the session

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            self._notify_single(listener)

    # --- mutation -----------------------------------------------------------
    def update(self, **fields: Any) -> "AppState":
        """Set any of the state's fields and notify subscribers once."""
        changed = False
        with self._lock:
            for key, value in fields.items():
                if not hasattr(self, key) or key.startswith("_"):
                    continue
                if getattr(self, key) != value:
                    setattr(self, key, value)
                    changed = True
            if changed:
                self.version += 1
        if changed:
            self._notify()
        return self

    def set_status(self, status: Status, operation: str | None = None) -> None:
        """Set the status (and optionally the current operation) and repaint."""
        fields: dict[str, Any] = {"status": status}
        if operation is not None:
            fields["operation"] = operation
        self.update(**fields)

    # --- activity/message stream -------------------------------------------
    def add_activity(self, kind: str, text: str, *, limit: int = 400) -> None:
        """Append one real activity line (bounded so a long task stays tidy)."""
        with self._lock:
            self.activities.append(Activity(kind=kind, text=text))
            if len(self.activities) > limit:
                del self.activities[: len(self.activities) - limit]
            self.version += 1
        self._notify()

    def add_message(self, role: str, text: str) -> None:
        with self._lock:
            self.messages.append({"role": role, "text": text})
            self.version += 1
        self._notify()

    # --- construction -------------------------------------------------------
    @classmethod
    def from_config(cls, config: Any, *, workspace: str = "") -> "AppState":
        """Build the initial state from live configuration (never faked).

        An unconfigured provider is reported as its real setup state: the
        provider label is empty when it is not ready, model is empty when none
        is selected, and the key is the masked form only.
        """
        state = cls()
        state.sync_from_config(config, workspace=workspace)
        return state

    def sync_from_config(self, config: Any, *, workspace: str | None = None) -> None:
        """Refresh the config-derived header values in one notification."""
        from ..core.modes import active_mode, mode_title
        from ..core.providers import provider_label, provider_ready, provider_requires_key

        provider_id = getattr(config, "provider", "") or ""
        ready = False
        try:
            ready = provider_ready(provider_id, config)
        except Exception:
            ready = False
        fields: dict[str, Any] = {
            "provider": provider_label(provider_id) if ready else "",
            "model": getattr(config, "model", "") or "",
            "mode": mode_title(active_mode(config)),
            "key_required": provider_requires_key(provider_id),
            "api_key": config.masked_key() if hasattr(config, "masked_key") else "",
        }
        if workspace is not None:
            fields["workspace"] = workspace
        try:
            fields["context_limit"] = int(config.effective_max_tokens())
        except Exception:
            fields["context_limit"] = int(getattr(config, "max_tokens", 0) or 0)
        self.update(**fields)
