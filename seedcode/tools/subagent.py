"""The ``subagent`` tool: real delegated agent execution (OpenCode parity).

Reference: ``OPENCODE/tmdp/packages/core/src/tool/plugin/subagent.ts`` — the
main agent delegates a task to a FRESH child session (isolated message
history), optionally with a different agent persona and model, and the
child's final report comes back as the tool result. Semantics ported here:

* **Context isolation** — the child engine starts with an empty message
  history: only the delegated ``task`` prompt and the system prompt of the
  chosen agent profile. Nothing the parent has said or done leaks in, and
  the child cannot pollute the parent's history (its whole transcript is a
  side conversation; only the final report becomes the tool output).
* **Depth guard** — a child agent cannot spawn further subagents (OpenCode
  routes nested spawn attempts through its own depth checks; ported here as
  an explicit engine-level refusal with a clear message). Depth is a session
  property, so a sleeping child cannot resurrect it later.
* **Permission floor** — the child runs with the SAME PermissionManager
  instance as its parent. Duties never grant permissions: a read-only
  profile stays read-only, and confirmations still surface to the user.
* **Failure isolation** — a child that errors out (provider failure, budget
  exhaustion, tool crash loop) surfaces a plain tool error to the parent so
  the parent can reason over it; the parent's own turn survives.

The engine attaches a delegation callback at construction
(:func:`register_delegate`); the tool itself stays a plain registry entry so
it is testable without a model, and hooks/whitelists govern it like any
other tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from .base import ToolError, ToolResult, register

if TYPE_CHECKING:
    from .permissions import PermissionManager

#: Where delegated-workflow reporting lands while a child is executing.
on_delegate: Callable[[str], None] | None = None

_delegate: Callable[..., str] | None = None


def register_delegate(delegate: Callable[..., str]) -> None:
    """Attach the engine-side spawn callback (one per engine, replaced later)."""
    global _delegate
    _delegate = delegate


def clear_delegate() -> None:
    """Remove the attached callback (session teardown / tests)."""
    global _delegate
    _delegate = None


@register(
    "subagent",
    "Delegate a task to a dedicated sub-agent (isolated context, own step "
    "budget) and return its final report. Optionally select an agent "
    "persona, e.g. 'explore' for read-only investigation, 'general' for a "
    "full-capability worker.",
    {
        "task": "the task description given to the sub-agent (self-contained; "
        "it does not see this conversation)",
        "agent": "(optional) agent id to run the task with "
        "(build/general/plan/explore or a user-defined agent; default general)",
    },
    mutates=True,
)
def _subagent(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    task = str(args.get("task") or "").strip()
    if not task:
        raise ToolError("the 'task' parameter is required — describe what to do, fully.")
    worker = str(args.get("agent") or "general").strip() or "general"
    if _delegate is None:
        raise ToolError(
            "No sub-agent runner is attached to this session "
            "(the interactive agent registers one at startup). Subagent "
            "delegation is unavailable here."
        )
    try:
        report = _delegate(task, worker)
    except ToolError:
        raise
    except Exception as exc:  # a broken child must not kill the parent loop
        raise ToolError(f"Subagent failed before returning a report: {exc}") from exc
    if not str(report or "").strip():
        raise ToolError("The subagent returned an empty report.")
    return ToolResult(True, f"Subagent ({worker}) report:\n{report}")
