"""/status — the live runtime state of the current session (v6.2.5).

One panel, every value read from real application state: the active provider
and its backend, the selected model, the current mode (Chat / Assist / Code),
the Code Mode workspace, the provider connection status, and where config and
project memory live. Nothing here is hardcoded or fabricated — a status of
"Not checked" means exactly that.
"""

from __future__ import annotations

from rich.table import Table

from .. import __version__
from ..codemode_state import codemode_state
from ..core.providers import PROVIDERS, provider_label
from ..core.providers.base import STATUS_UNKNOWN
from ..utils.helpers import config_path
from ..utils.terminal_env import detect_terminal
from . import CommandContext, CommandResult, command


def mode_label(config) -> str:
    """The active mode: Code Mode sharpens Assist, which sharpens Chat.

    Shared by /status, /mode, and /chat so every surface names the mode the
    same way from the same source of truth.
    """
    if codemode_state().enabled:
        return "Code Mode"
    return "Assist Mode" if config.agent_mode else "Chat"


# Backward-compatible private name.
_mode_label = mode_label


def _connection_label(config) -> str:
    """Provider connection status from the session cache (no network I/O)."""
    if not config.is_configured():
        return "Setup needed"
    provider = PROVIDERS.get(config.provider)
    status = provider.status if provider is not None else STATUS_UNKNOWN
    return status or STATUS_UNKNOWN


def _backend_label(config, provider) -> str:
    """Human connection type: local server, or the provider's API family."""
    if provider is None:
        return f"{provider_label(config.provider)} API"
    if provider.local:
        return "Local server"
    return provider.backend_label or f"{provider.label} API"


def _api_key_label(config, provider) -> str:
    """Key state for the active provider — masked, never the key itself.

    Providers that need no key (the built-in Default connection and local
    Ollama) say so explicitly instead of showing an empty or fake value.
    """
    if provider is None or not provider.requires_key:
        return "not required"
    return config.masked_key(provider.id)


@command("status", "Show the current runtime status")
def _status(ctx: CommandContext, arg: str) -> CommandResult:
    config = ctx.config
    provider = PROVIDERS.get(config.provider)

    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.dim", justify="right", no_wrap=True)
    table.add_column(style="seed.text")

    table.add_row("Version", __version__)
    table.add_row("Provider", provider_label(config.provider))
    table.add_row("Backend", _backend_label(config, provider))
    table.add_row("API Key", _api_key_label(config, provider))
    table.add_row("Model", config.model or "(none — run /model)")
    table.add_row("Mode", mode_label(config))
    table.add_row("Status", _connection_label(config))

    terminal = detect_terminal()
    table.add_row("Terminal", terminal.describe())

    workspace = codemode_state().workspace
    table.add_row("Workspace", str(workspace) if workspace is not None else "(Code Mode off)")
    table.add_row("Memory", ".seedcode" if workspace is not None else "(not active)")
    table.add_row("Config", str(config_path()))

    ctx.ui.panel(table, title="Runtime Status")
    if not config.is_configured():
        ctx.ui.dim("Not configured — run /provider to choose a provider and key.")
    return CommandResult()
