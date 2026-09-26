"""/mode — the generic switcher for the two canonical modes (v8.2.5).

/mode           — show the current mode.
/mode chat      — plain conversation (no tools).
/mode agent     — the unified autonomous workspace/coding agent.

``code``, ``codemode``, ``assist`` and ``desktop`` are accepted as input
aliases and resolve to Agent Mode — the retired Code/Assist modes are Agent
Mode now, so no third mode can be selected.

The individual commands (/chat, /agent, /codemode) remain the direct routes;
this command is the single entry point that understands all of them.
"""

from __future__ import annotations

from ..core.modes import MODE_DESCRIPTIONS, Mode, active_mode, mode_title, parse_mode
from . import CommandContext, CommandResult, command, show_session_bar
from .status import mode_label

#: Input spellings that name a mode the user may type.
_KNOWN = ("chat", "agent", "code", "codemode", "assist", "desktop")


@command("mode", "Show or switch the runtime mode. Usage: /mode [chat|agent]")
def _mode(ctx: CommandContext, arg: str) -> CommandResult:
    raw = arg.strip().lower()

    if not raw:
        ctx.ui.info(f"Mode: {mode_label(ctx.config)}")
        ctx.ui.dim("Switch with: /mode chat|agent")
        return CommandResult()

    mode = parse_mode(raw, default=Mode.CHAT)
    if raw not in _KNOWN:
        ctx.ui.warning("[Command Error] Invalid syntax.")
        ctx.ui.dim("Expected: /mode chat|agent")
        return CommandResult()

    if raw in ("code", "codemode", "code-mode"):
        # Code Mode's workspace coding capabilities are built into Agent Mode.
        ctx.ui.dim("(Code Mode is part of Agent Mode now — selecting Agent Mode)")

    if mode is Mode.CHAT:
        from .chat import _enter_chat

        _enter_chat(ctx)
    else:  # Mode.AGENT
        from .assist import enable_assist

        enable_assist(ctx.ui, ctx.config)

    ctx.ui.dim(f"Mode: {mode_title(active_mode(ctx.config))} — {MODE_DESCRIPTIONS[mode]}")
    show_session_bar(ctx.ui, ctx.config)
    return CommandResult()
