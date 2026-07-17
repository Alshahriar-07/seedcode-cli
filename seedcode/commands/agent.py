"""Agent-mode commands: /agent, /permission, /index, /tools."""

from __future__ import annotations

from rich.table import Table

from ..config import save_config
from ..tools import TOOL_REGISTRY, PermissionManager, PermissionMode
from ..tools.filesystem import build_index
from . import CommandContext, CommandResult, command


@command("agent", "Toggle agent mode. Usage: /agent [on|off]")
def _agent(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip().lower()
    if raw in ("on", "off"):
        enable = raw == "on"
    elif not raw:
        enable = not ctx.config.agent_mode  # bare /agent toggles
    else:
        ctx.ui.warning("Usage: /agent [on|off]")
        return CommandResult()

    ctx.config.agent_mode = enable
    save_config(ctx.config)
    if enable:
        mode = PermissionMode.parse(ctx.config.permission_mode)
        ctx.ui.success(f"Agent mode ON — permission: {mode.label} (/permission to change).")
        ctx.ui.dim("The assistant can now read, edit, search, and run commands here.")
    else:
        ctx.ui.success("Agent mode OFF — back to plain chat.")
    return CommandResult()


@command("permission", "Show or set the agent permission mode", aliases=("perm",))
def _permission(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip()
    if not raw:
        current = PermissionMode.parse(ctx.config.permission_mode)
        table = Table.grid(padding=(0, 3))
        table.add_column(style="seed.primary", no_wrap=True)
        table.add_column(style="seed.text")
        for mode in PermissionMode:
            marker = "●" if mode is current else " "
            detail = {
                PermissionMode.READ_ONLY: "inspect only — no writes, no commands",
                PermissionMode.WORKSPACE: "edit and run inside this directory only",
                PermissionMode.FULL_ACCESS: "no path restriction (use with care)",
            }[mode]
            table.add_row(f"{marker} {mode.value}", detail)
        ctx.ui.panel(table, title=f"Permission — {current.label}")
        ctx.ui.dim("Change with /permission <read_only|workspace|full_access>")
        return CommandResult()

    try:
        mode = PermissionMode.parse(raw)
    except ValueError as exc:
        ctx.ui.warning(str(exc))
        return CommandResult()
    ctx.config.permission_mode = mode.value
    save_config(ctx.config)
    ctx.ui.success(f"Permission mode set to {mode.label}.")
    return CommandResult()


@command("index", "Show a compact tree of the current project")
def _index(ctx: CommandContext, arg: str) -> CommandResult:
    perm = PermissionManager(mode=PermissionMode.READ_ONLY)
    ctx.ui.panel(build_index(perm), title="Project Index")
    return CommandResult()


@command("tools", "List the tools available in agent mode")
def _tools(ctx: CommandContext, arg: str) -> CommandResult:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.primary", no_wrap=True)
    table.add_column(style="seed.text")
    for name in sorted(TOOL_REGISTRY):
        tool = TOOL_REGISTRY[name]
        kind = "changes files/system" if tool.mutates else "read-only"
        table.add_row(name, f"{tool.description}  ({kind})")
    ctx.ui.panel(table, title="Agent Tools")
    return CommandResult()
