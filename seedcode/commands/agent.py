"""Agent Mode commands: /agent and /permission (v8.1.0).

/agent is the primary route into Agent Mode — the single general-purpose
execution mode that the retired Assist Mode folded into. /assist and
/desktop remain accepted aliases so an existing habit keeps working.
/permission keeps its dedicated interactive picker; /index and /tools stay
as inspection commands.
"""

from __future__ import annotations

from ..config import save_config
from ..tools import TOOL_REGISTRY, PermissionManager, PermissionMode
from ..tools.filesystem import build_index
from ..ui.selector import Option, select
from . import CommandContext, CommandResult, command, show_session_bar
from .assist import disable_assist, enable_assist


@command("agent", "Select Agent Mode (general-purpose execution). Usage: /agent [on|off]")
def _agent(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip().lower()
    if raw in ("on", "off"):
        enable = raw == "on"
    elif not raw:
        enable = not ctx.config.agent_mode  # bare /agent toggles
    else:
        ctx.ui.warning("[Command Error] Invalid syntax.")
        ctx.ui.dim("Expected: /agent on|off")
        return CommandResult()

    if enable:
        enable_assist(ctx.ui, ctx.config)
    else:
        disable_assist(ctx.ui, ctx.config)
    show_session_bar(ctx.ui, ctx.config)
    return CommandResult()


@command(
    "permission",
    "Show or set the tool permission level",
    aliases=("perm", "permissions"),
)
def _permission(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip()
    detail = {
        PermissionMode.READ_ONLY: "inspect only — no writes, no commands",
        PermissionMode.WORKSPACE: "edit and run inside this directory only",
        PermissionMode.DESKTOP: "control this computer (mouse, keyboard, apps)",
        PermissionMode.FULL_SYSTEM: "no path restriction + sensitive actions (use with care)",
    }
    if not raw:
        current = PermissionMode.parse(ctx.config.permission_mode)
        chosen = select(
            [
                Option(mode.label, mode.value_str, detail=detail[mode])
                for mode in PermissionMode
            ],
            title="Permission Level",
            initial=current.value_str,
            searchable=False,
            hint="↑↓ move   Enter select   Esc keep current",
        )
        if chosen is None:
            ctx.ui.dim(f"Permission unchanged ({current.label}).")
            return CommandResult()
        raw = str(chosen)

    try:
        mode = PermissionMode.parse(raw)
    except ValueError as exc:
        ctx.ui.warning(str(exc))
        return CommandResult()
    ctx.config.permission_mode = mode.value_str
    save_config(ctx.config)
    ctx.ui.success(f"Permission mode set to {mode.label}.")
    return CommandResult()


@command("index", "Show a compact tree of the current project")
def _index(ctx: CommandContext, arg: str) -> CommandResult:
    perm = PermissionManager(mode=PermissionMode.READ_ONLY)
    ctx.ui.panel(build_index(perm), title="Project Index")
    return CommandResult()


@command("tools", "List the tools available to the agent modes")
def _tools(ctx: CommandContext, arg: str) -> CommandResult:
    from ..ui.layout import columns_grid

    rows = []
    for name in sorted(TOOL_REGISTRY):
        tool = TOOL_REGISTRY[name]
        kind = "changes files/system" if tool.mutates else "read-only"
        rows.append((name, f"{tool.description}  ({kind})"))
    ctx.ui.panel(
        columns_grid(rows, ("seed.primary", "seed.text")), title="Agent Tools"
    )
    return CommandResult()
