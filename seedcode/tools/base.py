"""Tool contracts and registry for agent mode.

A tool is a named action with a JSON-friendly argument schema and a runner.
The registry is the single source of truth for what the agent can do; the
system prompt shown to the model is generated from it (:func:`tool_manifest`),
so the documentation the model sees can never drift from the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from ..utils.text import safe_text

if TYPE_CHECKING:
    from .permissions import PermissionManager

# Output larger than this is truncated before it re-enters the conversation,
# keeping a single tool call from blowing the model's context window.
MAX_OUTPUT_CHARS = 12_000


class ToolError(Exception):
    """A tool could not complete; the message is fed back to the model."""


def int_arg(args: dict[str, Any], name: str, default: int, lo: int, hi: int) -> int:
    """Coerce an integer argument, clamped to [lo, hi].

    Models sometimes send numbers as strings or send junk; a friendly
    :class:`ToolError` beats the raw ``ValueError`` the generic crash guard
    would otherwise surface.
    """
    raw = args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"Argument '{name}' must be a whole number, got {raw!r}.") from None
    return max(lo, min(value, hi))


@dataclass(slots=True)
class ToolResult:
    """Outcome of one tool execution, fed back into the conversation."""

    ok: bool
    output: str
    #: Unified diff of the real file change (empty for non-mutating tools).
    diff: str = ""
    #: The real process exit status, when the tool ran a command (v9.2.1).
    #: None for tools that do not execute a process, or whose process never
    #: exited (timeout/cancel) — never guessed.
    exit_code: int | None = None

    def __post_init__(self) -> None:
        # v7.1.0: a tool's output may contain a lone surrogate (a command that
        # emitted invalid UTF-8 bytes, a file read back with surrogates, a
        # model-supplied string). Every result is normalized at construction so
        # no consumer — model request, JSON, file, console — can ever fail to
        # encode it.
        self.output = safe_text(self.output)
        self.diff = safe_text(self.diff)

    def for_model(self) -> str:
        """Result text as the model sees it, truncated to a safe size."""
        text = self.output if self.output.strip() else "(no output)"
        if len(text) > MAX_OUTPUT_CHARS:
            omitted = len(text) - MAX_OUTPUT_CHARS
            text = text[:MAX_OUTPUT_CHARS] + f"\n... [truncated {omitted} chars]"
        status = "OK" if self.ok else "ERROR"
        return safe_text(f"[{status}] {text}")


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
    # Which engine the tool belongs to: "core" (files/search/git/shell) or
    # "desktop" (the Computer Engine). Drives manifest filtering so plain
    # agent mode never advertises desktop tools.
    group: str = "core"
    # arg name -> JSON-schema type for non-string args ("integer",
    # "boolean", ...). Unlisted args are strings. Drives native tool schemas.
    types: dict[str, str] = field(default_factory=dict)

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
    name: str,
    description: str,
    args: dict[str, str],
    *,
    mutates: bool,
    group: str = "core",
    types: dict[str, str] | None = None,
) -> Callable[[Runner], Runner]:
    """Decorator registering a runner in the tool table."""

    def wrap(runner: Runner) -> Runner:
        TOOL_REGISTRY[name] = Tool(
            name=name,
            description=description,
            args=args,
            mutates=mutates,
            runner=runner,
            group=group,
            types=types or {},
        )
        # A late registration (desktop module import) invalidates the cache.
        _SPECS_CACHE.clear()
        return runner

    return wrap


def register_alias(
    name: str,
    description: str,
    target: str,
    *,
    group: str | None = None,
) -> None:
    """Register ``name`` as an exact alias of an already-registered tool.

    The alias reuses the target's runner, arguments, JSON-schema types and
    mutation flag, so it can never drift from the implementation it names.
    Used for the equivalent tool names the agent contract promises (e.g.
    ``list_directory`` for ``list_dir``); the description states the mapping
    so the model knows both names do the same real work.
    """
    tool = TOOL_REGISTRY.get(target)
    if tool is None:  # pragma: no cover - registration-order bug
        raise KeyError(f"Cannot alias '{name}': unknown tool '{target}'.")
    register(
        name,
        f"{description} (same tool as {target})",
        dict(tool.args),
        mutates=tool.mutates,
        group=group or tool.group,
        types=dict(tool.types),
    )(tool.runner)


def get_tool(name: str) -> Tool:
    tool = TOOL_REGISTRY.get((name or "").strip().lower())
    if tool is None:
        known = ", ".join(sorted(TOOL_REGISTRY))
        raise ToolError(f"Unknown tool '{name}'. Available tools: {known}.")
    return tool


def tool_manifest(
    groups: tuple[str, ...] = ("core",),
    *,
    allowed: "frozenset[str] | None" = None,
) -> str:
    """Tool documentation injected into the agent system prompt.

    Only tools in ``groups`` are listed, so what the model is told matches
    what the session actually allows (e.g. desktop tools only appear when
    desktop mode is on). When ``allowed`` names a whitelist (an agent
    profile's capability restriction), tools outside it are excluded too —
    the model is never offered a tool its profile cannot call.
    """
    lines = []
    for tool in sorted(TOOL_REGISTRY.values(), key=lambda t: t.name):
        if tool.group not in groups:
            continue
        if allowed is not None and tool.name not in allowed:
            continue
        arg_desc = ", ".join(f'"{a}": {d}' for a, d in tool.args.items()) or "none"
        lines.append(f"- {tool.name}: {tool.description}\n  args: {arg_desc}")
    return "\n".join(lines)


# v6.2.0: specs are immutable for a given registry state, so cache them per
# group tuple instead of rebuilding JSON schemas on every agent step.
_SPECS_CACHE: dict[tuple[str, ...], list[dict[str, Any]]] = {}


def tool_specs(
    groups: tuple[str, ...] = ("core",),
    *,
    allowed: "frozenset[str] | None" = None,
) -> list[dict[str, Any]]:
    """Neutral name/description/parameters specs for native tool calling.

    Generated from the same registry as :func:`tool_manifest`, so the schema
    a provider sends can never drift from the implementation. Each entry is
    ``{"name", "description", "parameters"}`` with ``parameters`` a
    JSON-schema object; args whose description lacks "(optional)" are
    required. Results are cached per ``groups`` (the registry is static after
    import; the cache is invalidated if a tool is registered). When
    ``allowed`` names a whitelist (an agent profile's capability
    restriction), tools outside it are excluded.
    """
    def _build(grouped: tuple[str, ...]) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for tool in sorted(TOOL_REGISTRY.values(), key=lambda t: t.name):
            if tool.group not in grouped:
                continue
            properties: dict[str, Any] = {}
            required: list[str] = []
            for arg, desc in tool.args.items():
                properties[arg] = {
                    "type": tool.types.get(arg, "string"),
                    "description": desc,
                }
                if "(optional)" not in desc:
                    required.append(arg)
            specs.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                }
            )
        return specs

    key = tuple(groups)
    cached = _SPECS_CACHE.get(key)
    if cached is None or len(cached) != sum(1 for t in TOOL_REGISTRY.values() if t.group in key):
        cached = _build(key)
        _SPECS_CACHE[key] = cached
    if allowed is None:
        return list(cached)
    return [s for s in cached if s["name"] in allowed]
