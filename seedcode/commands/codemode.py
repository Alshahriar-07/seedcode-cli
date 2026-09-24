"""/codemode — workspace-aware Code Mode (v6.2.0).

/codemode on      — treat the CWD as the project workspace: ensure .seedcode,
                    refresh the index incrementally, and switch the agent to
                    workspace-aware coding behavior.
/codemode off     — back to the previous mode (memory stays on disk).
/codemode status  — workspace, memory, and index state.
"""

from __future__ import annotations

from rich.table import Table

from ..codemode_state import codemode_state
from ..config import save_config
from ..tools import PermissionMode
from . import CommandContext, CommandResult, command, show_session_bar


@command("codemode", "Workspace-aware Code Mode. Usage: /codemode [on|off|status]")
def _codemode(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip().lower()
    state = codemode_state()

    if raw in ("status", ""):
        table = Table.grid(padding=(0, 2))
        table.add_column(style="seed.dim", no_wrap=True)
        table.add_column(style="seed.text")
        for line in state.status_lines():
            if ":" in line:
                key, value = line.split(":", 1)
                table.add_row(key.strip(), value.strip())
            else:
                table.add_row("", line)
        ctx.ui.panel(table, title="Code Mode")
        if not state.enabled:
            ctx.ui.dim("Enable with: /codemode on")
        return CommandResult()

    if raw == "on":
        _enable_codemode(ctx)
        show_session_bar(ctx.ui, ctx.config)
        return CommandResult()

    if raw == "off":
        _disable_codemode(ctx)
        show_session_bar(ctx.ui, ctx.config)
        return CommandResult()

    ctx.ui.warning("[Command Error] Invalid syntax.")
    ctx.ui.dim("Expected: /codemode on|off|status")
    return CommandResult()


def _enable_codemode(ctx: CommandContext) -> None:
    config = ctx.config
    state = codemode_state()
    workspace = state.workspace  # already set? keep it (re-enable same project)

    # Agent Mode is the engine Code Mode sharpens; turn it on when off.
    if not config.agent_mode:
        from .assist import enable_assist

        enable_assist(ctx.ui, config)
    # Agent Mode prefers the desktop level when the engine exists; Code Mode is
    # a *coding* workflow, so keep the editing level (workspace) unless the user
    # had already chosen something stronger.
    if PermissionMode.parse(config.permission_mode) == PermissionMode.DESKTOP:
        config.permission_mode = PermissionMode.WORKSPACE.value_str
        save_config(config)

    from .. import codemode_state as cms

    result = cms.enable(workspace or _detect_workspace())
    ctx.ui.success("Code Mode ON")
    for line in result.status_lines()[1:]:
        ctx.ui.dim(f"  {line}")
    indexed = result.last_index.get("indexed", 0)
    unchanged = result.last_index.get("unchanged", 0)
    if indexed or unchanged:
        ctx.ui.dim(f"  Index: {indexed} indexed, {unchanged} unchanged (incremental)")
    ctx.ui.dim("  The agent now consults .seedcode memory + index before touching files.")
    # v7.1.0: the compact Code Mode header replaces the old tall banner — one
    # row while idle, showing the live state and the plan progress.
    console = getattr(ctx.ui, "console", None)
    if console is not None:
        from ..ui.codemode_header import render_code_mode_header

        render_code_mode_header(console)


def _disable_codemode(ctx: CommandContext) -> None:
    from .. import codemode_state as cms

    cms.disable()
    ctx.ui.success("Code Mode OFF — .seedcode memory kept for next session")


def _detect_workspace():
    from pathlib import Path

    return Path.cwd()


@command("workspace", "Show the active Code Mode workspace")
def _workspace(ctx: CommandContext, arg: str) -> CommandResult:
    state = codemode_state()
    if state.enabled and state.workspace is not None:
        ctx.ui.info(f"Workspace: {state.workspace}")
    else:
        ctx.ui.dim("Code Mode is off — enable with /codemode on")
    return CommandResult()
