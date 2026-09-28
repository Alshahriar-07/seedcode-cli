"""/codemode — the Agent Mode workspace coding capability (v9.1.0).

Code Mode is no longer a separate mode: its workspace-aware coding behaviour
is a native capability of Agent Mode. This command activates that capability
for the current directory.

/codemode on      — treat the CWD as the project workspace: ensure .seedcode,
                    refresh the index incrementally, and make Agent Mode
                    workspace-aware.
/codemode off     — deactivate the workspace capability (memory stays on disk).
/codemode status  — workspace, memory and index state.
"""

from __future__ import annotations

from rich.table import Table

from ..codemode_state import codemode_state
from ..config import save_config
from ..tools import PermissionMode
from . import CommandContext, CommandResult, command, context_cancel, show_session_bar


@command("codemode", "Agent Mode workspace capability. Usage: /codemode [on|off|status]")
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
        ctx.ui.panel(table, title="Project Workspace")
        if not state.enabled:
            ctx.ui.dim("Enable with: /codemode on (or /agent on)")
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
    cancel = context_cancel(ctx)

    def superseded() -> bool:
        return callable(cancel) and bool(cancel())

    if superseded():
        return
    # Agent Mode is the unified engine this capability belongs to; turn it on
    # when it is off so the workspace is never active without an agent.
    if not config.agent_mode:
        from .assist import enable_assist

        enable_assist(ctx.ui, config, cancel=cancel)
    if superseded():
        return
    # Workspace coding prefers the editing level (workspace) unless the user
    # had already chosen something stronger than desktop.
    if PermissionMode.parse(config.permission_mode) == PermissionMode.DESKTOP:
        config.permission_mode = PermissionMode.WORKSPACE.value_str
        save_config(config)

    from .. import codemode_state as cms

    # The index scan is cancellable: a switch the user has already reversed
    # stops it rather than walking the whole tree first.
    result = cms.enable(workspace or _detect_workspace(), cancel=cancel)
    ctx.ui.success("Workspace ON — Agent Mode is now workspace-aware")
    for line in result.status_lines()[1:]:
        ctx.ui.dim(f"  {line}")
    indexed = result.last_index.get("indexed", 0)
    unchanged = result.last_index.get("unchanged", 0)
    if indexed or unchanged:
        ctx.ui.dim(f"  Index: {indexed} indexed, {unchanged} unchanged (incremental)")
    ctx.ui.dim("  The agent now consults .seedcode memory + index before touching files.")


def _disable_codemode(ctx: CommandContext) -> None:
    from .. import codemode_state as cms

    cms.disable()
    ctx.ui.success("Workspace OFF — .seedcode memory kept for next session")


def _detect_workspace():
    from pathlib import Path

    return Path.cwd()


@command(
    "workspace",
    "Show or change the active workspace. Usage: /workspace [change | <path>]",
)
def _workspace(ctx: CommandContext, arg: str) -> CommandResult:
    """Show, pick, or switch the workspace that Agent Mode works in.

    The workspace is the single root for file reads/writes, indexing,
    ``.seedcode`` context and terminal commands, so changing it re-points the
    live capability and the UI header in one step. `/workspace change` opens the
    OS folder picker; `/workspace <path>` switches directly.
    """
    from ..workspace import active_workspace, pick_folder_native, set_workspace

    state = codemode_state()
    target = arg.strip()

    if target.lower() in ("change", "pick", "select", "choose", "browse"):
        chosen = pick_folder_native(active_workspace())
        if chosen is None:
            ctx.ui.dim(
                "No folder picker is available on this host — use: "
                "/workspace <path>"
            )
            return CommandResult()
        target = str(chosen)

    if not target:
        current = active_workspace()
        ctx.ui.info(f"Workspace: {current}")
        if state.enabled and state.workspace is not None:
            ctx.ui.dim(f"  .seedcode memory + index active at {state.workspace}")
        else:
            ctx.ui.dim("  Workspace capability is off — enable with /codemode on")
        ctx.ui.dim(
            "Change with: /workspace change (folder picker) or "
            "/workspace <path>"
        )
        return CommandResult()

    try:
        previous = active_workspace()
        selected = set_workspace(target)
    except (NotADirectoryError, OSError) as exc:
        ctx.ui.warning(f"[Command Error] {exc}")
        ctx.ui.dim("Expected an existing directory: /workspace <path>")
        return CommandResult()

    ctx.ui.success(f"Workspace changed: {previous} -> {selected}")
    # Re-point the live workspace capability so .seedcode, the index and the
    # agent's permissions all move with it; a disabled capability stays off.
    if state.enabled:
        from .. import codemode_state as cms

        result = cms.enable(selected)
        for line in result.status_lines()[1:]:
            ctx.ui.dim(f"  {line}")
    _refresh_ui_workspace(ctx, selected)
    return CommandResult()


def _refresh_ui_workspace(ctx: CommandContext, path) -> None:
    """Surface a workspace change in the persistent header (best-effort)."""
    try:
        tui = getattr(ctx.ui, "tui", None)
        state = getattr(tui, "state", None)
        if state is None:
            return
        state.update(workspace=str(path))
        invalidate = getattr(tui, "_invalidate", None)
        if callable(invalidate):
            invalidate()
    except Exception:  # a cosmetic refresh must never break the switch
        pass
