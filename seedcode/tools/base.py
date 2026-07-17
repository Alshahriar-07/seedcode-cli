"""Tool contracts and registry for agent mode.

A tool is a named action with a JSON-friendly argument schema and a runner.
The registry is the single source of truth for what the agent can do; the
system prompt shown to the model is generated from it (:func:`tool_manifest`),
so the documentation the model sees can never drift from the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .permissions import PermissionManager

# Output larger than this is truncated before it re-enters the conversation,
# keeping a single tool call from blowing the model's context window.
MAX_OUTPUT_CHARS = 12_000


class ToolError(Exception):
    """A tool could not complete; the message is fed back to the model."""


@dataclass(slots=True)
class ToolResult:
    """Outcome of one tool execution, fed back into the conversation."""

    ok: bool
    output: str

    def for_model(self) -> str:
        """Result text as the model sees it, truncated to a safe size."""
        text = self.output if self.output.strip() else "(no output)"
        if len(text) > MAX_OUTPUT_CHARS:
            omitted = len(text) - MAX_OUTPUT_CHARS
            text = text[:MAX_OUTPUT_CHARS] + f"\n... [truncated {omitted} chars]"
        status = "OK" if self.ok else "ERROR"
        return f"[{status}] {text}"


Runner = Callable[["PermissionManager", dict[str, Any]], ToolResult]


@dataclass(slots=True)
class Tool:
    """One agent-callable action."""

    name: str
    description: str
    # arg name -> one-line description; "(optional)" marks optional args.
    args: dict[str, str]
    # True when the tool changes files or system state (drives permissions).
    mutates: bool
    runner: Runner

    def run(self, permissions: "PermissionManager", args: dict[str, Any]) -> ToolResult:
        missing = [
            name
            for name, desc in self.args.items()
            if "(optional)" not in desc and name not in args
        ]
        if missing:
            raise ToolError(
                f"Tool '{self.name}' is missing required argument(s): {', '.join(missing)}."
            )
        return self.runner(permissions, args)


TOOL_REGISTRY: dict[str, Tool] = {}


def register(
    name: str, description: str, args: dict[str, str], *, mutates: bool
) -> Callable[[Runner], Runner]:
    """Decorator registering a runner in the tool table."""

    def wrap(runner: Runner) -> Runner:
        TOOL_REGISTRY[name] = Tool(
            name=name, description=description, args=args, mutates=mutates, runner=runner
        )
        return runner

    return wrap


def get_tool(name: str) -> Tool:
    tool = TOOL_REGISTRY.get((name or "").strip().lower())
    if tool is None:
        known = ", ".join(sorted(TOOL_REGISTRY))
        raise ToolError(f"Unknown tool '{name}'. Available tools: {known}.")
    return tool


def tool_manifest() -> str:
    """Tool documentation injected into the agent system prompt."""
    lines = []
    for tool in sorted(TOOL_REGISTRY.values(), key=lambda t: t.name):
        arg_desc = ", ".join(f'"{a}": {d}' for a, d in tool.args.items()) or "none"
        lines.append(f"- {tool.name}: {tool.description}\n  args: {arg_desc}")
    return "\n".join(lines)
