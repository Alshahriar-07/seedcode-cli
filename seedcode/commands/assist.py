"""Agent Mode: the unified autonomous workspace/coding agent (v8.2.5).

/agent on   — selects Agent Mode: the full capability set (AI, filesystem,
              terminal, git, browser, keyboard, mouse, windows, vision, OCR,
              desktop automation) plus the workspace coding capability
              (``.seedcode`` project memory + index, plan → execute → verify).
/agent off  — back to plain Chat Mode.

Agent Mode is the single general-purpose execution mode. The retired Assist
Mode and Code Mode are this mode under their old names: ``/assist``,
``/desktop`` and ``/codemode`` still work and route here, but they never
create a third mode.
"""

from __future__ import annotations

from rich.table import Table

from ..computer import is_available
from ..config import save_config
from ..tools import TOOL_REGISTRY, PermissionMode
from . import CommandContext, CommandResult, command, context_cancel, show_session_bar

# The Agent Mode capability set, in display order. Desktop-engine rows are
# marked so they can be dimmed when the Computer Engine is unavailable.
_CAPABILITIES: tuple[tuple[str, str, bool], ...] = (
    ("AI", "Chat, reasoning, and code generation", False),
    ("Filesystem", "Read, write, edit, search, and organise files", False),
    ("Terminal", "Run shell commands with live output", False),
    ("Git", "Status, diff, log, commit, push, pull", False),
    ("Browser", "Navigate, click, type, search", True),
    ("Keyboard", "Type, hotkeys, shortcuts", True),
    ("Mouse", "Move, click, drag, scroll", True),
    ("Windows", "List, focus, open, close", True),
    ("Vision", "See and understand the screen", True),
    ("OCR", "Read text from the screen", True),
    ("Desktop Automation", "Multi-step computer control", True),
)


@command("assist", "Legacy alias for Agent Mode. Usage: /assist [on|off]")
def _assist(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip().lower()
    if raw in ("on", "off"):
        enable = raw == "on"
    elif not raw:
        # Bare /assist: show status
        _show_status(ctx)
        return CommandResult()
    else:
        ctx.ui.warning("[Command Error] Invalid syntax.")
        ctx.ui.dim("Expected: /agent on|off (or /assist on|off)")
        return CommandResult()

    if enable:
        enable_assist(ctx.ui, ctx.config, cancel=context_cancel(ctx))
    else:
        disable_assist(ctx.ui, ctx.config)
    show_session_bar(ctx.ui, ctx.config)
    return CommandResult()


def capability_table(desktop_ok: bool) -> Table:
    """The Agent Mode capability list (✓ rows; unavailable rows dim).

    OCR is probed independently of the rest of the desktop stack: it needs a
    native engine the Python packages do not supply, so marking it available
    purely because the Computer Engine imports would tell the user something
    untrue.
    """
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", no_wrap=True)
    table.add_column()
    ocr_ok = _ocr_available() if desktop_ok else False
    for name, detail, needs_desktop in _CAPABILITIES:
        available = desktop_ok if needs_desktop else True
        if name == "OCR":
            available = ocr_ok
        if available:
            table.add_row(f"[seed.success]✓ {name}[/seed.success]", f"[seed.text]{detail}[/seed.text]")
        else:
            table.add_row(f"[seed.dim]○ {name}[/seed.dim]", f"[seed.dim]{detail}[/seed.dim]")
    return table


def _ocr_available() -> bool:
    """Whether the OCR tier can genuinely run (best-effort)."""
    try:
        from ..computer import ocr

        return ocr.available()
    except Exception:
        return False


def _ui_hook(ui, name: str):
    """A best-effort optional UI hook (``None`` when the UI has no such one).

    The persistent TUI exposes extra lifecycle hooks (initialization feedback)
    that the sequential console has no use for; calling them is never allowed
    to matter, so an absent or failing hook is simply skipped.
    """
    hook = getattr(ui, name, None)
    return hook if callable(hook) else None


def activate_agent_mode(config) -> None:
    """Switch to Agent Mode — the *cheap* half of ``/agent on``.

    Only the state that must change for the session to genuinely be in Agent
    Mode: the mode itself, persisted. This is what a UI callback may run
    synchronously; everything expensive is :func:`prepare_agent_mode`.
    """
    config.mode = "agent"
    save_config(config)


def _superseded(cancel) -> bool:
    """Whether ``cancel`` says this work has been replaced by a newer switch."""
    return callable(cancel) and bool(cancel())


def prepare_agent_mode(
    ui,
    config,
    *,
    desktop_ok: bool | None = None,
    cancel=None,
) -> bool:
    """The *expensive* half of Agent Mode activation.

    Safe on a background worker thread (and meant to be): the workspace
    capability walks the project, the capability panel probes the desktop
    stack, and the session permission prompt waits for the user. Nothing here
    may run inside a UI event callback — that is exactly what used to freeze
    the terminal, because the permission prompt waits for an answer the very
    event loop it was called from would have to deliver.

    ``cancel`` is polled as the work proceeds: a switch the user has already
    reversed stops here (and reports ``False``) instead of finishing and
    overwriting the newer state. Returns whether Agent Mode finished preparing.

    Agent Mode is the unified workspace/coding agent, so preparing it also
    activates the workspace capability (``.seedcode`` memory + index) that the
    former Code Mode provided.
    """
    cancelled = cancel if callable(cancel) else None
    if _superseded(cancelled):
        return False
    begin = _ui_hook(ui, "begin_initialization")
    if begin is not None:
        try:
            begin("Agent Mode")
        except Exception:
            pass
    try:
        # Desktop capabilities require the Computer Engine. When available,
        # Agent Mode runs at the ``desktop`` level so the AI can drive the
        # computer; otherwise it stays at ``workspace`` (AI + filesystem +
        # terminal + git still work).
        if desktop_ok is None:
            desktop_ok, desktop_reason = is_available()
        else:
            desktop_reason = ""
        if _superseded(cancelled):
            # Do not write config for a switch that has already been replaced:
            # a stale worker must never win the race with a newer one.
            return False
        config.permission_mode = (
            PermissionMode.DESKTOP.value_str if desktop_ok
            else PermissionMode.WORKSPACE.value_str
        )
        save_config(config)

        # The workspace coding capability (former Code Mode) is native to Agent
        # Mode: activate it for the current directory automatically.
        workspace_ready = _ensure_workspace(cancel=cancelled)
        if _superseded(cancelled):
            return False  # superseded: the newer switch owns the messages

        ui.success("Agent Mode ON")
        ui.blank()
        ui.panel(capability_table(desktop_ok), title="Agent Mode")
        if not desktop_ok:
            ui.dim(f"Desktop capabilities unavailable: {desktop_reason or 'unavailable'}")
        if workspace_ready:
            ui.dim("Project workspace ready — .seedcode memory + index active.")
        ui.blank()

        level_label = "desktop" if desktop_ok else "workspace"
        ui.dim(
            f"Permission level: {level_label} — change in Settings › Advanced "
            "or /permission."
        )

        # Ask for routine desktop control ONCE, here, instead of interrupting
        # every action later. Sensitive actions still confirm individually.
        # ``cancel`` abandons the ask if the user has already switched away.
        if desktop_ok and not _superseded(cancelled):
            request_session_permissions(ui, cancel=cancelled)
        if _superseded(cancelled):
            return False
        ui.dim("The AI picks the right tools for each task automatically.")
        return True
    finally:
        # Only the transition that is still current may clear the initializing
        # state; a superseded one must leave it to its successor (which either
        # finishes the preparation or has already gone back to Chat Mode).
        if not _superseded(cancelled):
            end = _ui_hook(ui, "end_initialization")
            if end is not None:
                try:
                    end()
                except Exception:
                    pass


def enable_assist(ui, config, *, cancel=None) -> None:
    """Select Agent Mode: the full capability set in one switch.

    The sequential-console route: the caller's thread is not the one the
    permission dialog needs, so the immediate and the expensive halves can run
    back to back here. ``cancel`` is the probe the TUI's background worker
    hands in; it is simply passed through. The persistent TUI never calls this
    on its event-loop thread — it activates (and then prepares) Agent Mode on a
    worker so the loop stays free.
    """
    if _superseded(cancel):
        return  # a superseded switch must not write the mode at all
    activate_agent_mode(config)
    prepare_agent_mode(ui, config, cancel=cancel)


def _ensure_workspace(cancel=None) -> bool:
    """Activate the ``.seedcode`` workspace capability for the CWD.

    Best-effort: a filesystem problem here must never stop Agent Mode from
    being selected. ``cancel`` stops a superseded index scan early. Returns
    whether the workspace is now active.
    """
    try:
        from pathlib import Path

        from .. import codemode_state as cms

        if cms.codemode_state().enabled:
            return True
        cms.enable(Path.cwd(), cancel=cancel)
        return True
    except Exception:
        return False


def request_session_permissions(ui, *, cancel=None) -> bool:
    """Request every routine desktop permission for the session, in one prompt.

    Returns whether the session-wide grant was given. Declining is safe: the
    engine simply falls back to confirming each action as it comes, which is
    the old behaviour. Sensitive actions (registry writes, secrets, system
    power, deletions, purchases) are never granted here — they always ask.

    ``cancel`` abandons the prompt when the mode switch that raised it has been
    superseded (the user switched again, or went back to Chat).
    """
    from ..computer.permissions import (
        CATEGORY_LABELS,
        SESSION_GRANT_CATEGORIES,
        session_permissions,
    )

    session = session_permissions()
    if session.requested:
        return bool(session.granted)

    wanted = "\n".join(
        f"  • {CATEGORY_LABELS.get(c, c)}" for c in SESSION_GRANT_CATEGORIES
    )
    description = (
        "Grant these for this session so Agent Mode doesn't interrupt every step:\n"
        f"{wanted}\n"
        "Sensitive actions (registry writes, passwords, system power, deletions, "
        "purchases) will still ask each time."
    )

    try:
        answer = ui.confirm_desktop(
            "Agent Mode session permissions", description, cancel=cancel
        )
    except TypeError:
        # A UI double without the ``cancel`` kwarg (older/plain adapters).
        try:
            answer = ui.confirm_desktop("Agent Mode session permissions", description)
        except Exception:
            return False
    except Exception:
        # No interactive prompt available (headless/non-TTY): leave the
        # per-action flow in place rather than silently granting anything.
        return False

    allowed = answer in ("y", "a")
    if allowed:
        session.request(SESSION_GRANT_CATEGORIES, allow=True)
        ui.dim("Desktop control granted for this session — you won't be asked again.")
    else:
        ui.dim("Declined: Agent Mode will ask before each desktop action.")
    return allowed


def disable_assist(ui, config, *, cancel=None) -> None:
    """Leave Agent Mode: back to plain Chat.

    The workspace capability is deactivated too, but the ``.seedcode`` memory
    stays on disk for the next Agent Mode session. Always cheap — this is what
    lets the TUI leave Agent Mode immediately, even with an initialization
    still in flight.
    """
    from ..computer.permissions import session_permissions

    if _superseded(cancel):
        return
    config.mode = "chat"
    # Drop back to the safe editing level (removes desktop capability).
    config.permission_mode = PermissionMode.WORKSPACE.value_str
    save_config(config)
    # The workspace coding capability (former Code Mode) belongs to Agent Mode.
    try:
        from .. import codemode_state as cms

        if cms.codemode_state().enabled:
            cms.disable()
    except Exception:
        pass
    # Forget the session-wide desktop grant: turning Agent Mode back on asks again.
    session_permissions().reset()
    ui.success("Agent Mode OFF — back to plain chat")


def _show_status(ctx: CommandContext) -> None:
    """Show current Agent Mode status."""
    from ..core.modes import active_mode, mode_title

    on = ctx.config.agent_mode
    desktop_ok, desktop_reason = is_available()

    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.dim", justify="right", no_wrap=True)
    table.add_column()

    state = "[seed.accent]ON[/seed.accent]" if on else "[seed.dim]OFF[/seed.dim]"
    table.add_row("Agent Mode", state)
    table.add_row("Mode", mode_title(active_mode(ctx.config)))
    if not desktop_ok:
        table.add_row("Desktop", f"Unavailable: {desktop_reason}")
    table.add_row("Permission", ctx.config.permission_mode.replace("_", " ").title())

    core_tools = [n for n, t in TOOL_REGISTRY.items() if t.group == "core"]
    desktop_tools = [n for n, t in TOOL_REGISTRY.items() if t.group == "desktop"]
    table.add_row("Available tools", f"{len(core_tools)} core, {len(desktop_tools)} desktop")

    ctx.ui.panel(table, title="Agent Mode")
    ctx.ui.blank()
    ctx.ui.dim("Toggle with: /agent on | /agent off (alias: /assist)")
