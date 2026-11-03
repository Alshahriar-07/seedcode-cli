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
    "state_role",
    "task_glyph",
    "tree_panel_lines",
    "tree_progress",
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
    "blocked": "◷",
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


#: A hierarchical row: (state, title, depth, task_id, has_children).
TreeRow = tuple[str, str, int, int, bool]

#: How a container row shows whether it is open or closed.
_EXPANDED = "▼"
_COLLAPSED = "▶"
_EXPANDED_ASCII = "v"
_COLLAPSED_ASCII = ">"


def tree_progress(
    rows: list[TreeRow], percent: int | None = None
) -> str:
    """The panel's progress line, from the graph's real states.

    ``percent`` is the orchestrator's own figure (verified leaf work); without
    it the percentage is derived from the rows themselves. Completed work only
    — a skipped task is never counted as done.
    """
    if not rows:
        return ""
    # A container is a summary row: the counts (and the graph's percentage)
    # describe real work, so only rows without children are counted.
    leaves = [row for row in rows if not bool(row[4])]
    counted = leaves or rows
    total = len(counted)
    done = sum(1 for row in counted if str(row[0]) == "completed")
    if percent is None:
        percent = int(round(100 * done / total)) if total else 0
    if done == total:
        return f"All tasks completed ({done}/{total})"
    return f"Progress: {int(percent)}% ({done} / {total} completed)"


def tree_panel_lines(
    rows: list[TreeRow],
    width: int,
    *,
    collapsed: set[int] | None = None,
    percent: int | None = None,
    legacy: bool = False,
) -> list[str]:
    """The right-side panel as plain-text lines, hierarchy included.

    Each row is indented by its depth, prefixed with the state glyph and (for
    a container) an expand/collapse arrow. Rows are clipped to the panel width
    and the progress line is appended. An empty plan renders nothing.
    """
    width = max(10, int(width or 10))
    if not rows:
        return []
    closed = {int(value) for value in (collapsed or ())}
    arrow_open = _EXPANDED_ASCII if legacy else _EXPANDED
    arrow_closed = _COLLAPSED_ASCII if legacy else _COLLAPSED
    lines: list[str] = []
    for state, title, depth, task_id, has_children in rows:
        glyph = task_glyph(str(state), legacy=legacy)
        indent = "  " * max(0, int(depth))
        arrow = ""
        if has_children:
            arrow = arrow_closed if int(task_id) in closed else arrow_open
            arrow += " "
        prefix = f"{indent}{arrow}{glyph} "
        lines.append(prefix + _clip(title, width - len(prefix)))
    progress = tree_progress(rows, percent)
    if progress:
        lines.append(_clip(progress, width))
    return lines


def plan_progress(rows: list[tuple[str, str]]) -> str:
    """The one-line progress summary (``3 / 7 completed`` or the all-done note)."""
    if not rows:
        return ""
    total = len(rows)
    done = sum(1 for state, _t in rows if str(state) == "completed")
    return "All tasks completed" if done == total else f"{done} / {total} completed"
