"""/chat — explicit Chat Mode (plain conversation, no tools).

/chat      — show the current mode.
/chat on   — leave Agent Mode and return to plain Chat: the AI answers with
             text only and never touches the filesystem or terminal.

Chat Mode is the default runtime mode; this command exists so a user can
always get back to it directly, without hunting through menus.
"""

from __future__ import annotations

from . import CommandContext, CommandResult, command, show_session_bar
from .status import mode_label


@command("chat", "Switch to plain Chat Mode. Usage: /chat [on]")
def _chat(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip().lower()
    if raw in ("", "status"):
        ctx.ui.info(f"Mode: {mode_label(ctx.config)}")
        ctx.ui.dim("Switch with: /chat on · /agent on (or /mode chat|agent)")
        return CommandResult()

    if raw != "on":
        ctx.ui.warning("[Command Error] Invalid syntax.")
        ctx.ui.dim("Expected: /chat [on]")
        return CommandResult()

    _enter_chat(ctx)
    show_session_bar(ctx.ui, ctx.config)
    return CommandResult()


def _enter_chat(ctx: CommandContext) -> None:
    """Return the session to plain Chat Mode (idempotent)."""
    from .assist import disable_assist

    # disable_assist also deactivates the workspace capability, so a chat
    # session never keeps an active project workspace.
    if ctx.config.agent_mode:
        disable_assist(ctx.ui, ctx.config)
    else:
        # Agent Mode was already off, but a workspace could still be active.
        from .. import codemode_state as cms

        if cms.codemode_state().enabled:
            cms.disable()
    ctx.ui.success("Chat Mode ON — plain conversation, no tools")
