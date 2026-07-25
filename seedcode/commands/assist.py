"""Assist Mode: unified AI + computer control.

/assist on  — enables the full capability set (AI, filesystem, terminal,
              git, browser, keyboard, mouse, windows, vision, OCR, desktop
              automation).
/assist off — back to plain chat.

Assist Mode is the ONLY automation mode Seed Code exposes. The old Agent
and Desktop modes were merged into it; their commands now route here.
"""

from __future__ import annotations

from rich.table import Table

from ..computer import is_available
from ..config import save_config
from ..tools import TOOL_REGISTRY, PermissionMode
from . import CommandContext, CommandResult, command

# The Assist capability set, in display order. Desktop-engine rows are
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


@command("assist", "Enable/disable Assist Mode (unified AI + computer control)")
def _assist(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip().lower()
    if raw in ("on", "off"):
        enable = raw == "on"
    elif not raw:
        # Bare /assist: show status
        _show_status(ctx)
        return CommandResult()
    else:
        ctx.ui.warning("Usage: /assist [on|off]")
        return CommandResult()

    if enable:
        enable_assist(ctx.ui, ctx.config)
    else:
        disable_assist(ctx.ui, ctx.config)
    return CommandResult()


def capability_table(desktop_ok: bool) -> Table:
    """The Assist capability list (✓ rows; unavailable rows dim).

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


def enable_assist(ui, config) -> None:
    """Enable Assist Mode: the full capability set in one switch."""
    config.agent_mode = True

    # Desktop capabilities require the Computer Engine. When available, Assist
    # runs at the ``desktop`` level so the AI can drive the computer; otherwise
    # it stays at ``workspace`` (AI + filesystem + terminal + git still work).
    desktop_ok, desktop_reason = is_available()
    config.permission_mode = (
        PermissionMode.DESKTOP.value_str if desktop_ok
        else PermissionMode.WORKSPACE.value_str
    )
    save_config(config)

    ui.success("Assist Mode ON")
    ui.blank()
    ui.panel(capability_table(desktop_ok), title="Assist Mode")
    if not desktop_ok:
        ui.dim(f"Desktop capabilities unavailable: {desktop_reason}")
    ui.blank()

    level_label = "desktop" if desktop_ok else "workspace"
    ui.dim(f"Permission level: {level_label} — change in Settings › Advanced or /permission.")

    # Ask for routine desktop control ONCE, here, instead of interrupting every
    # action later. Sensitive actions still confirm individually.
    if desktop_ok:
        request_session_permissions(ui)
    ui.dim("The AI picks the right tools for each task automatically.")


def request_session_permissions(ui) -> bool:
    """Request every routine desktop permission for the session, in one prompt.

    Returns whether the session-wide grant was given. Declining is safe: the
    engine simply falls back to confirming each action as it comes, which is
    the old behaviour. Sensitive actions (registry writes, secrets, system
    power, deletions, purchases) are never granted here — they always ask.
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
        "Grant these for this session so Assist doesn't interrupt every step:\n"
        f"{wanted}\n"
        "Sensitive actions (registry writes, passwords, system power, deletions, "
        "purchases) will still ask each time."
    )

    try:
        answer = ui.confirm_desktop("Assist Mode session permissions", description)
    except Exception:
        # No interactive prompt available (headless/non-TTY): leave the
        # per-action flow in place rather than silently granting anything.
        return False

    allowed = answer in ("y", "a")
    if allowed:
        session.request(SESSION_GRANT_CATEGORIES, allow=True)
        ui.dim("Desktop control granted for this session — you won't be asked again.")
    else:
        ui.dim("Declined: Assist will ask before each desktop action.")
    return allowed


def disable_assist(ui, config) -> None:
    """Disable Assist Mode: back to plain chat."""
    from ..computer.permissions import session_permissions

    config.agent_mode = False
    # Drop back to the safe editing level (removes desktop capability).
    config.permission_mode = PermissionMode.WORKSPACE.value_str
    save_config(config)
    # Forget the session-wide desktop grant: turning Assist back on asks again.
    session_permissions().reset()
    ui.success("Assist Mode OFF — back to plain chat")


def _show_status(ctx: CommandContext) -> None:
    """Show current Assist Mode status."""
    on = ctx.config.agent_mode
    desktop_ok, desktop_reason = is_available()

    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.dim", justify="right", no_wrap=True)
    table.add_column()

    state = "[seed.accent]ON[/seed.accent]" if on else "[seed.dim]OFF[/seed.dim]"
    table.add_row("Assist Mode", state)
    table.add_row("Mode", "Assist" if on else "Chat")
    if not desktop_ok:
        table.add_row("Desktop", f"Unavailable: {desktop_reason}")
    table.add_row("Permission", ctx.config.permission_mode.replace("_", " ").title())

    core_tools = [n for n, t in TOOL_REGISTRY.items() if t.group == "core"]
    desktop_tools = [n for n, t in TOOL_REGISTRY.items() if t.group == "desktop"]
    table.add_row("Available tools", f"{len(core_tools)} core, {len(desktop_tools)} desktop")

    ctx.ui.panel(table, title="Assist Mode")
    ctx.ui.blank()
    ctx.ui.dim("Toggle with: /assist on | /assist off")
