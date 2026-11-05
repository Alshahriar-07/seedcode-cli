"""Centralized reactive application state (v9.1.0).

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

from .briefing import Briefing

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
    INITIALIZING = "initializing"
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
    #: A mode change is being prepared on a background worker. The UI stays
    #: fully live while it runs; the mode itself has already switched.
    Status.INITIALIZING: "Initializing",
    Status.THINKING: "Thinking",
    Status.WORKING: "Working",
    Status.EXECUTING: "Running",
    Status.WAITING: "Waiting",
    Status.COMPLETED: "Completed",
    Status.ERROR: "Error",
    Status.CANCELLED: "Cancelled",
}

#: Monochrome terminal-safe status marks.
STATUS_MARKS: dict[Status, str] = {
    Status.READY: "+",
    Status.INITIALIZING: ".",
    Status.THINKING: ".",
    Status.WORKING: ".",
    Status.EXECUTING: ">",
    Status.WAITING: "...",
    Status.COMPLETED: "+",
    Status.ERROR: "!",
    Status.CANCELLED: "x",
}

#: ASCII fallbacks for consoles that cannot draw the glyphs.
STATUS_MARKS_ASCII: dict[Status, str] = {
    Status.READY: "+",
    Status.INITIALIZING: ".",
    Status.THINKING: ".",
    Status.WORKING: ".",
    Status.EXECUTING: ">",
    Status.WAITING: "...",
    Status.COMPLETED: "+",
    Status.ERROR: "!",
    Status.CANCELLED: "=",
}

#: Palette role used to colour each status.
STATUS_ROLES: dict[Status, str] = {
    Status.READY: "success",
    Status.INITIALIZING: "accent",
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
    """``(style, "> Working")`` for the status cell."""
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
    # v9.2.0: one unified coding agent — there is no Chat/Agent split.
    mode: str = "Agent"
    status: Status = Status.READY
    workspace: str = ""
    api_key: str = ""  # always the masked form; the real key never enters here
    key_required: bool = False
    context_limit: int = 0
    #: Real tokens currently held in the agent transcript (estimated from the
    #: messages actually sent). 0 before the first turn.
    context_used: int = 0
    connection: str = ""
    operation: str = ""
    agent_state: str = ""
    #: The model's reasoning level / effort when the configuration exposes
    #: one (``high``, ``medium``, …). Empty when it does not — the composer
    #: simply omits it rather than inventing a level.
    reasoning: str = ""
    #: True while a mode change is being prepared on a background worker. The
    #: UI is never blocked by it — this only says the work is still going on.
    agent_initializing: bool = False
    messages: list[dict[str, Any]] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)

    # --- v9.2.0: the right-side TASK PLAN panel -----------------------------
    #: ``(state, title)`` rows of the active task plan, straight from the
    #: session's task graph. Real orchestrator state — never an animation;
    #: empty while no plan is running, which hides the panel.
    plan_rows: list[tuple[str, str]] = field(default_factory=list)
    #: The same snapshot with its hierarchy: ``(state, title, depth, task_id,
    #: has_children)`` in tree order. This is what the panel renders (v9.3.1).
    plan_nodes: list[tuple[str, str, int, int, bool]] = field(default_factory=list)
    #: Verified-work percentage reported by the task graph itself.
    plan_percent: int = 0
    #: Container task ids the user collapsed (display-only, real ids).
    plan_collapsed: list[int] = field(default_factory=list)

    # --- internal execution status (not user-facing; see briefing.py) -------
    #: What the selected model is doing right now, set only from real events
    #: (see :mod:`seedcode.ui.briefing`). Idle until a turn really starts.
    briefing: Briefing = field(default_factory=Briefing)

    # --- v9.2.0: the thinking panel -----------------------------------------
    #: Reasoning text exposed by the model itself (a thinking channel such as
    #: Ollama's ``thinking`` field). Never synthesized by the UI; empty when
    #: the model exposes none.
    thinking_text: str = ""
    #: Collapsed state of the thinking panel (auto-collapsed at turn end; the
    #: user can re-expand it with Ctrl+B).
    thinking_open: bool = True
    #: Manual scroll offset into the thinking text (0 = follow the bottom).
    thinking_offset: int = 0
    #: Whether THIS turn streamed at least one (non-blank) model-exposed
    #: reasoning chunk. The panel never invents reasoning: a turn whose model
    #: exposed no reasoning records the honest fallback line instead (see
    #: :meth:`collapse_thinking`), and blank deltas never suppress it.
    reasoning_seen: bool = False

    #: Bounded size of the thinking buffer (characters): old reasoning text
    #: is dropped, never unbounded.
    THINKING_BUFFER_LIMIT = 24_000

    #: The single truthful line shown for a model that exposed no reasoning
    #: stream during a turn (v9.3.2). Nothing else is ever fabricated.
    REASONING_FALLBACK = "Reasoning stream unavailable for this model."

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

    # --- plan / thinking mutations (v9.2.0) -----------------------------------
    def set_plan(self, rows: list[tuple[str, str]]) -> None:
        """Publish one flat task-plan snapshot (the right-side panel).

        A snapshot replaces the previous one in full — a task that has since
        failed or been skipped is never silently shown as pending again.
        """
        normalized = [
            (str(getattr(s, "value", s) or "pending"), str(t or "")) for (s, t) in rows
        ][:40]
        nodes = [(state, title, 0, index, False) for index, (state, title) in enumerate(normalized, start=1)]
        self.set_plan_tree(nodes, plan_rows=normalized)

    def set_plan_tree(
        self,
        nodes: list[tuple[Any, Any, Any, Any, Any]],
        *,
        percent: int | None = None,
        plan_rows: list[tuple[str, str]] | None = None,
    ) -> None:
        """Publish the task graph's hierarchical snapshot (v9.3.1).

        ``nodes`` are ``(state, title, depth, task_id, has_children)`` rows in
        tree order, straight from ``TaskGraph.tree_rows()``; ``percent`` is the
        graph's own verified-work figure. ``plan_rows`` is derived for the flat
        compatibility view unless the caller supplies it.
        """
        normalized: list[tuple[str, str, int, int, bool]] = []
        for node in nodes:
            state, title, depth, task_id, has_children = node
            normalized.append(
                (
                    str(getattr(state, "value", state) or "pending"),
                    str(title or ""),
                    max(0, int(depth or 0)),
                    int(task_id or 0),
                    bool(has_children),
                )
            )
        normalized = normalized[:40]
        flat = plan_rows if plan_rows is not None else [(n[0], n[1]) for n in normalized]
        if percent is None:
            total = len(normalized)
            done = sum(1 for state, *_rest in normalized if state == "completed")
            percent = int(round(100 * done / total)) if total else 0
        with self._lock:
            changed = (
                normalized != self.plan_nodes
                or flat != self.plan_rows
                or int(percent) != self.plan_percent
            )
            if changed:
                self.plan_nodes = normalized
                self.plan_rows = flat
                self.plan_percent = int(percent)
                # Forget collapsed ids that no longer exist (a new plan).
                live = {n[3] for n in normalized if n[4]}
                self.plan_collapsed = [i for i in self.plan_collapsed if i in live]
                self.version += 1
        if changed:
            self._notify()

    def toggle_plan_collapse(self, task_id: int | None = None) -> None:
        """Collapse/expand a container task (all containers when no id)."""
        with self._lock:
            containers = {n[3] for n in self.plan_nodes if n[4]}
            if not containers:
                return
            if task_id is None:
                if containers - set(self.plan_collapsed):
                    self.plan_collapsed = sorted(containers | set(self.plan_collapsed))
                else:
                    self.plan_collapsed = []  # everything closed -> open all
            elif int(task_id) in self.plan_collapsed:
                self.plan_collapsed = [i for i in self.plan_collapsed if i != int(task_id)]
            elif int(task_id) in containers:
                self.plan_collapsed = sorted([*self.plan_collapsed, int(task_id)])
            else:
                return
            self.version += 1
        self._notify()

    # --- live briefing (v9.3.1) ----------------------------------------------
    def set_briefing(self, **fields: Any) -> None:
        """Update the bottom LIVE AI BRIEFING from one real execution event."""
        allowed = {
            "active",
            "status",
            "task",
            "activity",
            "last_completed",
            "next_action",
            "retry",
            "error",
        }
        changed = False
        with self._lock:
            for key, value in fields.items():
                if key not in allowed:
                    continue
                if getattr(self.briefing, key) != value:
                    setattr(self.briefing, key, value)
                    changed = True
            if changed:
                self.version += 1
        if changed:
            self._notify()

    def reset_briefing(self, status: str = "") -> None:
        """Return the briefing to idle (no request in flight)."""
        with self._lock:
            self.briefing.clear()
            if status:
                self.briefing.status = status
            self.version += 1
        self._notify()

    def reset_thinking(self) -> None:
        """Start of a new turn: a fresh thinking stream, panel expanded."""
        with self._lock:
            self.thinking_text = ""
            self.thinking_offset = 0
            self.thinking_open = True
            self.reasoning_seen = False
            self.version += 1
        self._notify()

    def begin_reasoning(self) -> None:
        """Start a request's reasoning window without touching the buffer.

        A plan session may keep its thinking history across turns, but the
        reasoning *accounting* is per request: whatever the previous request
        streamed must not suppress this one's honest fallback record.
        """
        with self._lock:
            self.reasoning_seen = False
            self.version += 1
        self._notify()

    def finish_thinking(self) -> None:
        """End-of-turn record: no reasoning streamed → the honest fallback.

        A turn that ends through :meth:`collapse_thinking` gets the same
        record there; plan sessions (which keep their buffer across turns)
        call this when the request finishes. Idempotent and never fabricates
        anything beyond the single truthful line.
        """
        with self._lock:
            if not self.reasoning_seen and not self.thinking_text.strip():
                self.thinking_text = self.REASONING_FALLBACK
                self.version += 1
        self._notify()

    def collapse_thinking(self) -> None:
        """Turn end (or user action): collapse the bounded thinking panel.

        If nothing in the turn exposed a reasoning stream (a model/provider
        without a thinking channel, or a turn that failed before any delta
        arrived), the completed panel records the single honest line instead
        of silently disappearing: the preserved thinking buffer is always
        truthful. The record is idempotent — repeated end-of-turn collapses
        never duplicate it.
        """
        with self._lock:
            if not self.reasoning_seen and not self.thinking_text.strip():
                self.thinking_text = self.REASONING_FALLBACK
                self.version += 1
            if self.thinking_open:
                self.thinking_open = False
                self.version += 1
        self._notify()

    def toggle_thinking(self) -> None:
        """User action (Ctrl+B): collapse / expand the activity panel."""
        with self._lock:
            if self.activities or self.thinking_text or self.briefing.active:
                self.thinking_open = not self.thinking_open
                if self.thinking_open:
                    self.thinking_offset = 0
                self.version += 1
        self._notify()

    def append_thinking(self, chunk: str) -> None:
        """Fold one model-exposed reasoning chunk into the bounded buffer."""
        if not (chunk or "").strip():
            # A blank delta is not real reasoning: it never suppresses the
            # honest fallback and never enters the buffer.
            return
        changed = False
        with self._lock:
            self.reasoning_seen = True
            self.thinking_text += chunk
            if len(self.thinking_text) > self.THINKING_BUFFER_LIMIT:
                # Drop the oldest half; the buffer stays bounded forever.
                self.thinking_text = self.thinking_text[len(self.thinking_text) // 2 :]
            changed = True
            self.version += 1
        if changed:
            self._notify()

    def scroll_thinking(self, lines: int) -> None:
        """User manual scroll of the thinking panel (older content up)."""
        with self._lock:
            self.thinking_offset = max(0, self.thinking_offset + int(lines))
            self.version += 1
        self._notify()

    def thinking_follow(self) -> bool:
        """True while the panel auto-follows the bottom (offset == 0).

        While the user has scrolled up the panel keeps their position — new
        reasoning does not yank it down — and auto-scroll resumes when they
        return to the bottom.
        """
        with self._lock:
            return self.thinking_offset == 0

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
            # v9.2.0: one unified agent — the header reports the capability,
            # not a mode selector.
            "mode": "Agent",
            "key_required": provider_requires_key(provider_id),
            "api_key": config.masked_key() if hasattr(config, "masked_key") else "",
        }
        if workspace is not None:
            fields["workspace"] = workspace
        try:
            if provider_id == "ollama" and int(
                getattr(config, "ollama_num_ctx", 0) or 0
            ) > 0:
                # The real context window requested from Ollama, not the
                # output-token budget (which is a different limit).
                fields["context_limit"] = int(config.ollama_num_ctx)
            else:
                fields["context_limit"] = int(config.effective_max_tokens())
        except Exception:
            fields["context_limit"] = int(getattr(config, "max_tokens", 0) or 0)
        self.update(**fields)
