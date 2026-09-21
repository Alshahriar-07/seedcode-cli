"""Compact startup hints (v6.2.5).

The main screen shows its runtime state (provider / model / mode / status)
**exactly once**, inside the dashboard panel. Everything here is a single-line
hint, deliberately small so the input prompt stays near the top of the screen
and the whole startup fits comfortably inside a standard 80x24 terminal.

Nothing in this module owns state: it reads the live command registry so the
hint can never advertise a command the router does not have.
"""

from __future__ import annotations

from rich.text import Text

# The commands advertised on the main screen, as a compact one-liner. Detailed
# descriptions live behind /help — not on the dashboard.
_COMMAND_ORDER: tuple[str, ...] = (
    "/help",
    "/status",
    "/codemode",
    "/assist",
    "/provider",
    "/model",
)

# The controls line for the interactive menu screen.
CONTROLS_HINT = "↑↓ Navigate   Enter Select   / Commands   Ctrl+K Palette   Ctrl+/ Help"
# The controls line for the chat/command input.
INPUT_HINT = "/help for commands   •   /exit for the menu   •   Ctrl+K palette   Ctrl+P files"


def render_command_hint(console) -> None:
    """One compact line of slash commands, filtered to those that exist."""
    from ..commands import _REGISTRY

    names = [name for name in _COMMAND_ORDER if name.lstrip("/") in _REGISTRY]
    if not names:
        return
    line = Text(no_wrap=True, overflow="crop")
    line.append("Commands  ", style="seed.dim")
    line.append("  ".join(names), style="seed.text")
    console.print(line)


__all__ = ["CONTROLS_HINT", "INPUT_HINT", "render_command_hint"]
