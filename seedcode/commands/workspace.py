"""/workspace and agent-capability commands — the unified coding agent (v9.2.0).

The old mode commands (``/chat``, ``/agent``, ``/code``, ``/codemode``,
``/desktop``, ``/assist``) are retired: Seed Code is one coding agent and has
no runtime modes to switch. Everything a session needs is here:

* ``/workspace``  — the active project (name, path, index/memory state) and
  interactive project-folder selection on demand;
* ``/permission`` — the tool permission level (read-only / workspace / full);
* ``/index``      — a compact tree of the active project;
* ``/tools``      — the tool registry the agent uses;
* ``/memory``     — the ``.seedcode`` project memory panel.

Desktop automation is deliberately gone from the capability list in v9.2.0,
which is why this module carries none of the Computer Engine wiring its
predecessor (:mod:`seedcode.commands.agent` / :mod:`.assist`) had.
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from .. import codemode_state as cms
from ..config import save_config
from ..tools import TOOL_REGISTRY, PermissionLevel, PermissionManager, PermissionMode
from . import CommandContext, CommandResult, command


def _active_project() -> str:
    """A human label for the active project (path, or 'none selected')."""
    state = cms.codemode_state()
    if state.workspace is not None:
        return str(state.workspace)
    return Path.cwd()


@command(
    "workspace",
    "Show the active project, or select a different project folder",
    aliases=("project", "project_folder", "folder", "ws"),
)
def _workspace(ctx: CommandContext, arg: str) -> CommandResult:
    """``/workspace`` (status) or ``/workspace <path>`` / ``/workspace select``."""
    state = cms.codemode_state()
    if arg.strip():
        target = arg.strip().strip('"')
        try:
            from ..workspace import set_workspace

            path = set_workspace(target)
        except Exception as exc:  # not a directory / inaccessible — report, never crash
            ctx.ui.error(f"[Command Error] {exc}")
            return CommandResult()
        state = _activate_project(path)
        ctx.ui.success(f"Active project: {path.name} — {path}")
    else:
        _show_project(ctx)
    return CommandResult()


def _activate_project(path: Path):
    """Bind the session to ``path``: .seedcode memory + index, then return state."""
    state = cms.codemode_state()
    state.workspace = path
    state.store = cms.SeedcodeStore(path)
    state.store.ensure()
    try:
        state.last_index = state.store.refresh_index()
    except OSError:
        state.last_index = {"indexed": 0, "unchanged": 0, "partial": False}
    state.enabled = True
    return state


def _show_project(ctx: CommandContext) -> None:
    """The /workspace status panel: real project state, nothing invented."""
    state = cms.codemode_state()
    workspace = state.workspace
    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.dim", justify="right", no_wrap=True)
    table.add_column(style="seed.text")

    if workspace is None:
        table.add_row("Project", "(none selected — run /workspace select)")
    else:
        table.add_row("Project", workspace.name)
        table.add_row("Path", str(workspace))
        store = state.store
        memories = len(store.list_memories()) if store else 0
        indexed = len(store.load_file_map()) if store else 0
        table.add_row("Memory", f".seedcode ({memories} note(s))")
        last = state.last_index or {}
        if last:
            table.add_row(
                "Index",
                f"{indexed} file(s) — last refresh: "
                f"{last.get('indexed', 0)} new/updated",
            )
        else:
            table.add_row("Index", f"{indexed} file(s)")
    ctx.ui.panel(table, title="Active Project")


@command(
    "permission",
    "Show or set the tool permission level",
    aliases=("perm", "permissions"),
)
def _permission(ctx: CommandContext, arg: str) -> CommandResult:
    from ..ui.layout import columns_grid
    from ..ui.selector import Option, select

    raw = arg.strip()
    detail = {
        PermissionLevel.READ_ONLY: "inspect only — no writes, no commands",
        PermissionLevel.WORKSPACE: "edit and run inside this project only",
        PermissionLevel.FULL_SYSTEM: "no path restriction + sensitive actions (use with care)",
    }
    if not raw:
        current = PermissionMode.parse(ctx.config.permission_mode)
        chosen = select(
            [
                Option(mode.label, mode.value_str, detail=detail[mode])
                for mode in PermissionLevel
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
    from ..tools.filesystem import build_index

    perm = PermissionManager(mode=PermissionLevel.READ_ONLY)
    ctx.ui.panel(build_index(perm), title="Project Index")
    return CommandResult()


@command("tools", "List the tools available to the coding agent")
def _tools(ctx: CommandContext, arg: str) -> CommandResult:
    from ..ui.layout import columns_grid

    rows = []
    for name in sorted(TOOL_REGISTRY):
        tool = TOOL_REGISTRY[name]
        kind = "changes files/system" if tool.mutates else "read-only"
        rows.append((name, f"{tool.description}  ({kind})"))
    ctx.ui.panel(columns_grid(rows, ("seed.primary", "seed.text")), title="Agent Tools")
    return CommandResult()


@command("memory", "Show the .seedcode project memory and recent session notes")
def _memory(ctx: CommandContext, arg: str) -> CommandResult:
    state = cms.codemode_state()
    store = state.store
    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.dim", justify="right", no_wrap=True)
    table.add_column(style="seed.text")
    if store is None or not state.enabled:
        table.add_row("Status", "(no active project — select one with /workspace)")
    else:
        memories = store.list_memories()
        summaries = store.latest_session_summaries(limit=3)
        table.add_row("Project memory", f"{len(memories)} note(s) in .seedcode/memory")
        if summaries:
            latest = summaries[0]
            table.add_row(
                "Last session",
                f"{str(latest.get('goal', ''))[:60]} — {str(latest.get('outcome', ''))[:60]}",
            )
    ctx.ui.panel(table, title="Project Memory")
    return CommandResult()
