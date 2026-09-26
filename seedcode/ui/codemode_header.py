"""Compact Code Mode header (v7.1.0): the professional agent status block.

The old Code Mode screen was a tall banner. This is the replacement — two rows
while working, one row while idle, no decorative art::

    ┌─ SEEDCODE 8.2.5 • CODE MODE ────────────────────────┐
    │ ● RUNNING   Task 3/8   Build authentication          │
    │   ████████████░░░░  72%   • 4m 32s • 6 calls        │
    └──────────────────────────────────────────────────────┘

    ┌─ SEEDCODE 8.2.5 • CODE MODE ────────────────────────┐
    │ ● READY     0/0 tasks                                │
    └──────────────────────────────────────────────────────┘

The version in the panel title comes from :data:`seedcode.__version__`, so
the header always matches the build it was shipped in.

The task view below it shows the plan as a short checklist (``✓ ● ○ ✗``) and a
single live activity line, so a long-running session stays readable:

    ✓ Setup project
    ✓ Create database
    ● Implement authentication
    ○ Build dashboard
    ○ Add tests

Everything here is display code driven by real session state; it never invents
progress. Narrow terminals get a one-line form, and consoles that cannot draw
the glyphs get the ASCII rendering of the same information.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from rich import box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from .. import __version__
from ..utils.text import safe_text

__all__ = [
    "CodeModeHeader",
    "fit_width",
    "format_duration",
    "live_action_line",
    "progress_bar",
    "render_code_mode_header",
    "task_checklist",
]

# Outer width of the design; never stretches wider than this.
HEADER_WIDTH = 64
# Below this the bordered panel no longer fits: use the single-line header.
MIN_PANEL_WIDTH = 34

_BAR_CELLS = 16
_BAR_FILL = "█"
_BAR_EMPTY = "░"
_BAR_FILL_ASCII = "#"
_BAR_EMPTY_ASCII = "-"

_STATE_STYLES = {
    "ready": "seed.dim",
    "planning": "seed.accent",
    "running": "seed.accent",
    "verifying": "seed.accent",
    "recovering": "seed.warning",
    "paused": "seed.warning",
    "blocked": "seed.warning",
    "failed": "seed.error",
    "completed": "seed.success",
    "cancelled": "seed.warning",
}
_STATE_MARKS = {
    "ready": ("●", "o"),
    "planning": ("●", ">"),
    "running": ("●", ">"),
    "verifying": ("●", ">"),
    "recovering": ("↻", ">"),
    "paused": ("‖", "||"),
    "blocked": ("■", "!"),
    "failed": ("✗", "x"),
    "completed": ("✓", "[ok]"),
    "cancelled": ("■", "!"),
}


def fit_width(terminal_width: int | None) -> int:
    """The header width for a terminal width: never wider than the design.

    Used on every refresh, so a terminal resize is picked up instead of leaving
    a panel wider than the screen (the panel falls back to the single-line form
    once the fitted width drops below :data:`MIN_PANEL_WIDTH`).
    """
    try:
        width = int(terminal_width or HEADER_WIDTH)
    except (TypeError, ValueError):
        width = HEADER_WIDTH
    return max(8, min(width, HEADER_WIDTH))


def _clip(value: str, limit: int) -> str:
    # v7.1.0: titles/activities come from model and tool text, so normalize
    # them here — a lone surrogate must never reach the console encoder.
    value = " ".join(safe_text(value).split())
    if limit <= 1:
        return ""
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def format_duration(seconds: float) -> str:
    """A short duration: ``42s``, ``4m 32s``, ``1h 04m``."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def progress_bar(percent: int, *, cells: int = _BAR_CELLS, legacy: bool = False) -> str:
    """A compact progress bar (ASCII on legacy consoles)."""
    percent = max(0, min(100, int(percent)))
    cells = max(4, int(cells))
    filled = int(round(cells * percent / 100))
    if percent > 0 and filled == 0:
        filled = 1  # never show an empty bar for real progress
    fill = _BAR_FILL_ASCII if legacy else _BAR_FILL
    empty = _BAR_EMPTY_ASCII if legacy else _BAR_EMPTY
    return fill * filled + empty * (cells - filled)


def task_checklist(
    rows: Iterable[tuple[str, str]],
    *,
    legacy: bool = False,
    limit: int = 12,
    marks: dict[str, str] | None = None,
) -> list[Text]:
    """Checklist lines for ``(state, title)`` rows.

    ``state`` is a task-state value (``completed``/``running``/…); ``marks``
    lets the caller supply an alternative glyph table (the agent's own
    :data:`seedcode.core.tasks.GLYPHS`).
    """
    from ..core.tasks import TaskState, glyph_for

    lines: list[Text] = []
    items = list(rows)[: max(1, limit)]
    for state, title in items:
        try:
            resolved = state if isinstance(state, TaskState) else TaskState(str(state))
        except ValueError:
            resolved = TaskState.PENDING
        mark = glyph_for(resolved, legacy=legacy)
        if marks:
            mark = marks.get(str(getattr(resolved, "value", resolved)), mark)
        style = {
            TaskState.COMPLETED: "seed.success",
            TaskState.FAILED: "seed.error",
            TaskState.BLOCKED: "seed.warning",
            TaskState.CANCELLED: "seed.dim",
            TaskState.RUNNING: "seed.accent",
            TaskState.VERIFYING: "seed.accent",
            TaskState.RECOVERING: "seed.warning",
            TaskState.SKIPPED: "seed.dim",
        }.get(resolved, "seed.dim")
        line = Text(no_wrap=True, overflow="crop")
        line.append(f"{mark} ", style=style)
        line.append(
            _clip(str(title), 60),
            style="seed.text" if resolved is TaskState.COMPLETED else style,
        )
        lines.append(line)
    return lines


def live_action_line(action: str, *, legacy: bool = False) -> Text | None:
    """The single live activity line (``→ Editing src/auth/session.ts``)."""
    action = " ".join(str(action or "").split())
    if not action:
        return None
    line = Text(no_wrap=True, overflow="crop")
    line.append("-> " if legacy else "→ ", style="seed.accent")
    line.append(_clip(action, 64), style="seed.text")
    return line


class CodeModeHeader:
    """The compact status block shown while Code Mode works."""

    def __init__(
        self,
        *,
        width: int = HEADER_WIDTH,
        legacy: bool = False,
        state: str = "ready",
        task_index: int = 0,
        task_total: int = 0,
        task_title: str = "",
        percent: int = 0,
        elapsed_s: float = 0.0,
        calls: int = 0,
        activity: str = "",
        mode_label: str = "Code Mode",
    ) -> None:
        # Kept as given: the width decides between the compact panel and the
        # one-line form (see ``render``), and a narrow terminal must never be
        # given a panel wider than itself.
        self.width = max(8, int(width))
        self.legacy = legacy
        self.state = state
        self.task_index = int(task_index)
        self.task_total = int(task_total)
        self.task_title = task_title
        self.percent = max(0, min(100, int(percent)))
        self.elapsed_s = float(elapsed_s)
        self.calls = int(calls)
        self.activity = activity
        # The mode this compact block reports. Agent Mode is the unified
        # mode now, but the same block is reused for a legacy Code Mode view.
        self.mode_label = mode_label or "Code Mode"

    # --- mutation ------------------------------------------------------------
    def update(self, **fields) -> "CodeModeHeader":
        """Set any of the header's fields (unknown names are ignored)."""
        for key, value in fields.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self

    @property
    def label(self) -> str:
        return self.state.upper()

    # --- rendering -----------------------------------------------------------
    def _mark(self) -> tuple[str, str]:
        marks = _STATE_MARKS.get(self.state, ("●", ">"))
        return (marks[1], marks[0]) if self.legacy else (marks[0], marks[0])

    def _status_text(self) -> Text:
        mark = self._mark()[0]
        style = _STATE_STYLES.get(self.state, "seed.dim")
        text = Text(no_wrap=True, overflow="crop")
        text.append(f"{mark} {self.label}", style=style)
        return text

    def _task_text(self) -> str:
        if self.task_total <= 0:
            return "0/0 tasks"
        return f"Task {self.task_index}/{self.task_total}"

    def lines(self) -> list[Text]:
        """The header's inner rows (one when idle, two while working)."""
        bullet = " * " if self.legacy else " • "
        inner = max(self.width - 4, 16)

        status = self._status_text()
        body = Text(no_wrap=True, overflow="crop")
        body.append_text(status)
        body.append("   ", style="seed.dim")
        body.append(self._task_text(), style="seed.text")
        title_room = inner - body.cell_len - 3
        if self.task_title and title_room > 8:
            body.append("   ", style="seed.dim")
            body.append(_clip(self.task_title, title_room), style="seed.text")
        rows = [body]

        if self.state in ("running", "verifying", "planning", "recovering"):
            bar = progress_bar(self.percent, legacy=self.legacy)
            bar_style = "seed.success" if self.percent >= 100 else "seed.accent"
            detail = Text(no_wrap=True, overflow="crop")
            detail.append("  ", style="seed.dim")
            detail.append(bar, style=bar_style)
            detail.append(f"  {self.percent}%", style="seed.text")
            detail.append(bullet + format_duration(self.elapsed_s), style="seed.dim")
            detail.append(bullet + f"{self.calls} calls", style="seed.dim")
            rows.append(detail)

        if self.activity:
            line = live_action_line(self.activity, legacy=self.legacy)
            if line is not None:
                line.truncate(inner, overflow="crop")
                rows.append(line)

        return rows

    def title(self) -> str:
        separator = " * " if self.legacy else " • "
        name = _clip(self.mode_label, 24).upper()
        return _clip(
            f"SEEDCODE {__version__}{separator}{name}", max(self.width - 10, 12)
        )

    def renderable(self) -> RenderableType:
        inner = max(self.width - 4, 16)
        width = max(self.width, MIN_PANEL_WIDTH)
        rows = self.lines()
        for line in rows:
            line.truncate(inner, overflow="crop")
        return Panel(
            Group(*rows),
            title=self.title(),
            title_align="left",
            border_style="seed.primary",
            box=box.ASCII if self.legacy else box.ROUNDED,
            padding=(0, 1),
            expand=False,
            width=width,
        )

    def plain_line(self) -> Text:
        """The one-line form used when even the compact panel does not fit."""
        line = Text(no_wrap=True, overflow="crop")
        line.append_text(self._status_text())
        line.append(f"  {self._task_text()}", style="seed.text")
        if self.task_title:
            line.append("  ", style="seed.dim")
            line.append(_clip(self.task_title, 24), style="seed.text")
        if self.state in ("running", "verifying", "planning", "recovering"):
            line.append(f"  {self.percent}%", style="seed.text")
        line.truncate(self.width, overflow="crop")
        return line

    def render(self, console) -> None:
        """Print the header (panel or single line) to ``console``."""
        if self.width >= MIN_PANEL_WIDTH:
            console.print(self.renderable())
        else:
            console.print(self.plain_line())


def checklist_width(rows: Sequence[tuple[str, str]]) -> int:
    """The longest title length in a checklist (used for layout decisions)."""
    return max((len(str(title)) for _, title in rows), default=0)


def render_code_mode_header(console, *, state: str = "ready", **fields) -> None:
    """Print the compact Code Mode header for the live console (best-effort)."""
    try:
        from .layout import supports_unicode

        width = min(getattr(console.size, "width", HEADER_WIDTH) or HEADER_WIDTH, HEADER_WIDTH)
        header = CodeModeHeader(
            width=width,
            legacy=not supports_unicode(console),
            state=state,
            **fields,
        )
        header.render(console)
    except Exception:
        pass  # a cosmetic header must never break a mode switch
