"""The ``update_task`` tool: persist the real plan to ``.seedcode/todo.json``."""

from __future__ import annotations

from ..core.task_state import TaskStateStore
from .base import ToolError, ToolResult, register


@register(
    "update_task",
    "Create or update the durable task plan in .seedcode/todo.json. Use it "
    "after inspecting the workspace to record the ordered steps, then after "
    "each meaningful result to mark the current step and what actually "
    "happened. This is a working record; completion still needs tool evidence.",
    {
        "task": "The overall task statement (optional).",
        "steps": "JSON array of steps, e.g. "
        '[{"description": "Inspect project", "status": "pending"}] (optional).',
        "status": "Task status: pending|in_progress|completed|failed|cancelled "
        "(optional).",
        "current_step": "1-based step id currently being worked on (optional).",
        "last_result": "One-line result of the last real operation (optional).",
    },
    mutates=False,
    types={"current_step": "integer"},
)
def update_task(permissions, args) -> ToolResult:
    store = TaskStateStore(permissions.workspace)
    state = store.update_from_tool(args)
    if not state.task and not state.steps:
        raise ToolError(
            "Nothing to save: provide the task statement, a steps array, or a "
            "status/current_step update."
        )
    if not store.save(state):
        raise ToolError(
            "Could not write .seedcode/todo.json — check workspace permissions."
        )
    return ToolResult(True, state.render() + f"\nSaved to {store.path}")
