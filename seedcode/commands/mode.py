"""/mode — one generic switcher for the runtime modes.

/mode           — show the current mode.
/mode chat      — plain conversation (no tools).
/mode assist    — the unified AI + tools mode (filesystem, terminal, git, ...).
/mode code      — Assist sharpened for the current workspace (Code Mode).
/mode agent     — legacy alias of assist.

The individual commands (/chat, /assist, /codemode) remain the direct routes;
this command is the single entry point that understands all of them.
"""

from __future__ import annotations

from . import CommandContext, CommandResult, command
from .status import mode_label


@command("mode", "Show or switch the runtime mode. Usage: /mode [chat|assist|code|agent]")
def _mode(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip().lower()

    if not raw:
        ctx.ui.info(f"Mode: {mode_label(ctx.config)}")
        ctx.ui.dim("Switch with: /mode chat|assist|code|agent")
        return CommandResult()

    if raw == "chat":
        from .chat import _enter_chat

        _enter_chat(ctx)
    elif raw == "assist":
        from .assist import enable_assist

        enable_assist(ctx.ui, ctx.config)
    elif raw == "code":
        from .codemode import _enable_codemode

        _enable_codemode(ctx)
    elif raw == "agent":
        # Agent Mode merged into Assist Mode; keep the name working.
        from .assist import enable_assist

        enable_assist(ctx.ui, ctx.config)
        ctx.ui.dim("(Agent Mode is Assist Mode now — same engine, same tools)")
    else:
        ctx.ui.warning("[Command Error] Invalid syntax.")
        ctx.ui.dim("Expected: /mode chat|assist|code|agent")

    return CommandResult()
