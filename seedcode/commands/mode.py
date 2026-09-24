"""/mode — the generic switcher for the three canonical modes (v8.1.0).

/mode           — show the current mode.
/mode chat      — plain conversation (no tools).
/mode code      — the workspace coding agent (Code Mode).
/mode agent     — general-purpose execution (Agent Mode).

``assist`` is accepted as an input alias and resolves to Agent Mode — the
retired Assist Mode is Agent Mode now, so no fourth mode can be selected.

The individual commands (/chat, /codemode, /agent) remain the direct routes;
this command is the single entry point that understands all of them.
"""

from __future__ import annotations

from ..core.modes import MODE_DESCRIPTIONS, Mode, active_mode, mode_title, parse_mode
from . import CommandContext, CommandResult, command, show_session_bar
from .status import mode_label


@command("mode", "Show or switch the runtime mode. Usage: /mode [chat|code|agent]")
def _mode(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip().lower()

    if not raw:
        ctx.ui.info(f"Mode: {mode_label(ctx.config)}")
        ctx.ui.dim("Switch with: /mode chat|code|agent")
        return CommandResult()

    mode = parse_mode(raw, default=Mode.CHAT)
    if raw not in ("chat", "code", "agent", "assist", "codemode"):
        ctx.ui.warning("[Command Error] Invalid syntax.")
        ctx.ui.dim("Expected: /mode chat|code|agent")
        return CommandResult()

    if raw == "assist":
        ctx.ui.dim("(Assist Mode is Agent Mode now — selecting Agent Mode)")

    if mode is Mode.CHAT:
        from .chat import _enter_chat

        _enter_chat(ctx)
    elif mode is Mode.CODE:
        from .codemode import _enable_codemode

        _enable_codemode(ctx)
    else:  # Mode.AGENT
        from .assist import enable_assist

        enable_assist(ctx.ui, ctx.config)

    ctx.ui.dim(f"Mode: {mode_title(active_mode(ctx.config))} — {MODE_DESCRIPTIONS[mode]}")
    show_session_bar(ctx.ui, ctx.config)
    return CommandResult()
