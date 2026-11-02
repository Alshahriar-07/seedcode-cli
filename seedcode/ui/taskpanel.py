"""The right-side task plan panel (v9.2.0).

The task plan the agent is executing — the session's TODO list — renders as a
dedicated panel on the RIGHT SIDE of the persistent TUI while the agent works.
Every row comes from the session's own task graph (``graph.checklist()``), so
the panel is driven by real orchestrator events, never by hardcoded UI
animations, and a task is never shown as done merely because the model said
so — the glyph reflects the *verified* state the session recorded.

States (see :class:`seedcode.core.tasks.TaskState`):

* ``○ Pending``          — waiting in plan order
* ``● In Progress``      — running / verifying / recovering
* ``✓ Completed``        — verified with real execution evidence
* ``✗ Failed``           — failed and not (yet) recovered
* ``↻ Retrying``         — blocked, waiting on its prerequisites' repair
* ``– Skipped``          — consciously not needed; never counted as work
* ``■ Cancelled``        — stopped by the user

The module is pure rendering: functions in, fragments out, no state, no
prompt_toolkit import, trivially testable in isolation.
"""

from __future__ import annotations

__all__ = [
    "PANEL_TITLE",
    "plan_panel_lines",
    "plan_progress",
    "task_glyph",
    "state_role",
]

#: The panel's fixed title (shown in the frame border, never animated).
PANEL_TITLE = " TASK PLAN "

# --- glyphs -------------------------------------------------------------------
#: TaskState.value -> mark (matches the spec's ○ ● ✓ ✗ states).
_GLYPHS: dict[str, str] = {
    "pending": "○",
    "running": "●",
    "verifying": "●",
    "recovering": "↻",
    "blocked": "↻",
    "failed": "✗",
    "completed": "✓",
    "skipped": "–",
    "cancelled": "■",
}

#: ASCII fallbacks for legacy consoles.
_GLYPHS_ASCII: dict[str, str] = {
    "pending": "o",
    "running": ">",
    "verifying": ">",
    "recovering": ">",
    "blocked": ">",
    "failed": "x",
    "completed": "+",
    "skipped": "-",
    "cancelled": "=",
}

#: Palette role per state (resolved by the caller's stylesheet mapping).
_ROLE_BY_STATE: dict[str, str] = {
    "pending": "dim",
    "running": "accent",
    "verifying": "accent",
    "recovering": "warning",
    "blocked": "warning",
    "failed": "error",
    "completed": "success",
    "skipped": "dim",
    "cancelled": "dim",
}


def task_glyph(state: str, *, legacy: bool = False) -> str:
    """The checklist mark for one plan state (ASCII on legacy consoles)."""
    return (_GLYPHS_ASCII if legacy else _GLYPHS).get(state or "", "○")


def state_role(state: str) -> str:
    """The palette role the state renders in."""
    return _ROLE_BY_STATE.get(state or "", "text")


# --- rows ---------------------------------------------------------------------
#: A row is (state, title) — exactly what TaskGraph.checklist() produces.
PlanRow = tuple[str, str]


def _clip(text: str, width: int) -> str:
    text = " ".join(str(text or "").split())
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def plan_panel_lines(rows: list[tuple[str, str]], width: int) -> list[str]:
    """The panel's plain-text lines (used by tests and the ASCII fallback).

    Layout: one line per task (glyph + title, clipped to the panel width),
    then the progress counter, or ``All tasks completed`` when every plan
    entry is resolved. A snapshot with no rows renders nothing.
    """
    width = max(10, int(width or 10))
    lines: list[str] = []
    for state, title in rows:
        glyph = task_glyph(str(state))
        lines.append(f"{glyph} {_clip(title, width - 3)}")
    if not lines:
        return lines
    total = len(rows)
    # Only VERIFIED work counts: a skipped task is never completed work.
    done = sum(
        1 for state, _t in rows if str(state) == "completed"
    )
    resolved = sum(
        1 for state, _t in rows if str(state) in ("completed", "skipped")
    )
    if done == total:
        lines.append("All tasks completed")
    else:
        lines.append(f"{done} / {total} completed")
        if resolved > done:
            lines.append(f"({resolved - done} skipped)")
    return lines


def plan_progress(rows: list[tuple[str, str]]) -> str:
    """The one-line progress summary (``3 / 7 completed`` or the all-done note)."""
    if not rows:
        return ""
    total = len(rows)
    done = sum(1 for state, _t in rows if str(state) == "completed")
    return "All tasks completed" if done == total else f"{done} / {total} completed"
