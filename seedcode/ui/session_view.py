"""Session inspection view (v7.1.0): the real state of a Code Mode session.

The live task view (:mod:`.tasks`) is deliberately compact — it shows the
current state, the plan as a checklist and one live action line. This module is
the *detailed* view behind ``/session`` and ``/status``: the same state, read
from real objects, with room for the per-task execution records the task engine
now keeps.

Two renderables, both built strictly from live data:

* :func:`summary_rows` — state, current task, progress, elapsed time, model and
  tool calls, retries, tests, blockers and the checkpoint/resumability of a
  :class:`~seedcode.core.session.CodeSession`, or of a plan restored from
  ``.seedcode/plan.json`` when nothing is running;
* :func:`task_rows` — one row per task: state, title, verification result and
  the task's own record (files inspected/affected, commands, tests, tool calls,
  retries, duration).

Nothing here invents progress. A value that the engine did not observe is
reported as absent (``—``), never guessed, and a task that did nothing says
"no tool activity recorded".

Rendering rules: every string passes :func:`~seedcode.utils.text.safe_text` (so
a lone surrogate in a model-written title can never raise), state marks fall
back to ASCII on consoles that cannot draw them, and no line is allowed to
exceed the owning console's width.
"""

from __future__ import annotations

from typing import Any

from rich.table import Table
from rich.text import Text

from ..core.tasks import Task, TaskGraph, TaskState, command_ok, glyph_for
from ..utils.text import safe_text
from .codemode_header import format_duration

__all__ = [
    "format_elapsed",
    "session_table",
    "state_label",
    "summary_rows",
    "task_rows",
    "tasks_table",
]

#: What an unobserved value looks like. Never a fabricated number.
_ABSENT = "—"
_ABSENT_LEGACY = "-"
#: The task-state styles, shared with the live view.
_STYLES: dict[TaskState, str] = {
    TaskState.COMPLETED: "seed.success",
    TaskState.RUNNING: "seed.accent",
    TaskState.VERIFYING: "seed.accent",
    TaskState.RECOVERING: "seed.warning",
    TaskState.BLOCKED: "seed.warning",
    TaskState.FAILED: "seed.error",
    TaskState.CANCELLED: "seed.dim",
    TaskState.PENDING: "seed.dim",
}

_DEFAULT_LIMIT = 12
_TITLE_CLIP = 44
_RECORD_CLIP = 74


def _clip(value: str, limit: int) -> str:
    """Clip a display value; the underlying value is never modified."""
    value = " ".join(safe_text(value).split())
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)] + "…"


def _absent(legacy: bool) -> str:
    return _ABSENT_LEGACY if legacy else _ABSENT


def state_label(state: TaskState, *, legacy: bool = False) -> str:
    """``✓ COMPLETED`` / ``● RUNNING`` / ... for one task state."""
    return f"{glyph_for(state, legacy=legacy)} {state.value.upper()}"


def format_elapsed(seconds: float) -> str:
    """Compact elapsed time (``0s`` before anything started)."""
    return format_duration(seconds)


# --- the session summary -----------------------------------------------------
def summary_rows(
    session: Any = None,
    *,
    checkpoint: dict[str, Any] | None = None,
    legacy: bool = False,
) -> list[tuple[str, str]]:
    """``(label, value)`` rows describing a session — live or checkpointed.

    ``session`` is a live :class:`~seedcode.core.session.CodeSession` (or any
    object exposing the same attributes); when it is absent the rows are
    reconstructed from a persisted checkpoint so a stopped session can still be
    inspected. Only observed values are emitted.
    """
    rows: list[tuple[str, str]] = []
    absent = _absent(legacy)
    graph: TaskGraph | None = getattr(session, "graph", None)

    if session is not None:
        status = getattr(session, "status", None)
        status_value = getattr(status, "value", status) or "running"
        rows.append(("State", str(status_value).upper()))
        done, total = graph.progress() if graph is not None else (0, 0)
        rows.append(("Progress", f"{done}/{total} tasks verified"))
        active = graph.active() if graph is not None else None
        if active is not None:
            rows.append(("Current task", f"{active.id}. {_clip(active.title, _TITLE_CLIP)}"))
            if active.current_action:
                rows.append(("Action", _clip(active.current_action, _TITLE_CLIP)))
        minutes = format_elapsed(float(getattr(session, "elapsed_s", 0.0) or 0.0))
        rows.append(("Elapsed", minutes))
        rows.append(("Model calls", str(int(getattr(session, "model_calls", 0) or 0))))
        rows.append(("Tool calls", str(int(getattr(session, "tool_calls", 0) or 0))))
        rows.append(("Recoveries", str(int(getattr(session, "recoveries", 0) or 0))))
    elif checkpoint:
        rows.append(("State", str(checkpoint.get("status") or "unknown").upper()))
        progress = checkpoint.get("progress") or {}
        rows.append(
            (
                "Progress",
                f"{int(progress.get('completed', 0))}/{int(progress.get('total', 0))} "
                "tasks verified",
            )
        )
        current = checkpoint.get("current_task") or {}
        if current:
            rows.append(
                (
                    "Current task",
                    f"{current.get('id')}. {_clip(str(current.get('title') or ''), _TITLE_CLIP)}",
                )
            )
        rows.append(("Model calls", str(int(checkpoint.get("model_calls") or 0))))
        rows.append(("Tool calls", str(int(checkpoint.get("tool_calls") or 0))))

    # --- evidence observed across the session (never a model claim) ----------
    evidence = getattr(session, "session_evidence", None)
    if evidence is not None:
        tests = list(getattr(evidence, "tests", []) or [])
        if tests:
            rows.append(
                (
                    "Tests",
                    f"passed ({_clip(tests[-1].command, 40)})"
                    if tests[-1].ok
                    else f"FAILED ({_clip(tests[-1].command, 40)})",
                )
            )
        else:
            rows.append(("Tests", absent))
        commands = list(getattr(evidence, "commands", []) or [])
        if commands:
            ok = sum(1 for c in commands if c.ok)
            rows.append(("Commands", f"{ok}/{len(commands)} ok"))
        files = list(getattr(evidence, "changed_files", []) or [])
        rows.append(("Files changed", str(len(files)) if files else absent))
        inspected = list(getattr(evidence, "inspected", []) or [])
        rows.append(("Files inspected", str(len(inspected)) if inspected else absent))
        errors = list(getattr(evidence, "errors", []) or [])
        if errors:
            rows.append(("Last error", _clip(errors[-1], _RECORD_CLIP)))

    state_blockers = getattr(getattr(session, "state", None), "blockers", None)
    if state_blockers:
        rows.append(("Blockers", _clip(str(state_blockers[-1]), _RECORD_CLIP)))
    if session is not None and getattr(session, "reason", ""):
        rows.append(("Reason", _clip(str(session.reason), _RECORD_CLIP)))

    if checkpoint:
        rows.append(("Checkpoint", "resumable — /resume continues this session"))
        rows.append(
            ("Next action", _clip(str(checkpoint.get("next_action") or ""), _RECORD_CLIP))
        )
    elif session is not None:
        rows.append(("Checkpoint", "saved on pause/stop (see /resume)"))
    return rows


# --- the per-task records ----------------------------------------------------
def task_rows(graph: TaskGraph | None, *, limit: int = _DEFAULT_LIMIT) -> list[Task]:
    """The plan's tasks in dependency order, capped for display."""
    if graph is None:
        return []
    return list(graph.tasks[: max(1, int(limit))])


def task_record_cells(
    task: Task, *, legacy: bool = False
) -> tuple[str, str, str]:
    """``(id, verification, execution record)`` for one task.

    The record is the task's own observed data, clipped for display.
    """
    mark = glyph_for(task.state, legacy=legacy)
    identifier = f"{mark} {task.id}"
    verification = (
        _clip(task.verification, 30) if task.verification else _absent(legacy)
    )
    if task.state is TaskState.PENDING and not task.started_at:
        return identifier, verification, "not started"

    # What the task actually did. An empty list is reported honestly: a task
    # that ran but observed nothing must not read as work.
    observed: list[str] = []
    if task.inspected:
        observed.append(f"{len(task.inspected)} inspected")
    if task.files_affected:
        observed.append(f"{len(task.files_affected)} file(s)")
    if task.commands:
        ok = sum(1 for label in task.commands if command_ok(label))
        observed.append(f"{ok}/{len(task.commands)} cmd ok")
    if task.tests:
        observed.append(
            "tests passed" if task.tests[-1].endswith(": passed") else "tests FAILED"
        )
    if task.tool_calls:
        observed.append(f"{task.tool_calls} calls")
    if task.attempts:
        observed.append(f"{task.attempts} retr{'y' if task.attempts == 1 else 'ies'}")

    parts = observed or ["no tool activity recorded"]
    if observed and task.started_at:
        parts.append(format_elapsed(task.duration_s))
    if task.errors:
        parts.append(f"error: {_clip(task.errors[-1], 40)}")
    if task.blocker:
        parts.append(f"blocked: {_clip(task.blocker, 40)}")
    return identifier, verification, _clip("; ".join(parts), _RECORD_CLIP)


# --- tables ------------------------------------------------------------------
def _grid(*styles: str) -> Table:
    """A headerless grid with one styled column per entry."""
    table = Table.grid(padding=(0, 2))
    for style in styles:
        table.add_column(style=style, no_wrap=False)
    return table


def session_table(
    session: Any = None,
    *,
    checkpoint: dict[str, Any] | None = None,
    legacy: bool = False,
) -> Table:
    """The ``/session`` summary grid (label / value)."""
    table = _grid("seed.dim", "seed.text")
    for label, value in summary_rows(session, checkpoint=checkpoint, legacy=legacy):
        table.add_row(
            Text(str(label), no_wrap=True), Text(_clip(str(value), _RECORD_CLIP))
        )
    return table


def tasks_table(graph: TaskGraph | None, *, legacy: bool = False) -> Table:
    """The per-task grid: state, title, verification and the real record."""
    table = _grid("seed.dim", "seed.text", "seed.dim", "seed.dim")
    if graph is None or not graph.tasks:
        table.add_row(Text(_absent(legacy)), Text("no plan yet"), Text(""), Text(""))
        return table
    for task in task_rows(graph):
        identifier, verification, record = task_record_cells(task, legacy=legacy)
        style = _STYLES.get(task.state, "seed.text")
        table.add_row(
            Text(identifier, style=style, no_wrap=True),
            Text(_clip(task.title, _TITLE_CLIP), style="seed.text"),
            Text(verification, style="seed.dim"),
            Text(record, style="seed.dim"),
        )
    return table
