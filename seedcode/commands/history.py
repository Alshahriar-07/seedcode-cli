"""Data-inspection and settings commands: /history, /config, /settings."""

from __future__ import annotations

from pydantic import ValidationError
from rich.table import Table

from ..config import save_config
from ..core.providers import PROVIDERS, ProviderError, get_provider, provider_label
from ..memory import list_sessions
from ..ui.prompts import read_line
from . import CommandContext, CommandResult, command

# Settings editable via /settings, with a tiny parser per value type.
# (provider/model have their own dedicated commands with live validation.)
_SETTINGS = {
    "username": str,
    "stream": bool,
    "ollama_host": str,
    "max_tokens": int,
}


@command("history", "List the active provider's saved sessions")
def _history(ctx: CommandContext, arg: str) -> CommandResult:
    # History is per provider: only the active backend's sessions are shown.
    sessions = list_sessions(ctx.config.provider)
    if not sessions:
        ctx.ui.dim(f"No saved sessions for {provider_label(ctx.config.provider)} yet.")
        return CommandResult()
    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.accent")
    table.add_column(style="seed.dim")
    for sid, count in sessions[:20]:
        table.add_row(sid, f"{count} messages")
    ctx.ui.panel(table, title=f"History — {provider_label(ctx.config.provider)}")
    return CommandResult()


@command("config", "Show current configuration")
def _config(ctx: CommandContext, arg: str) -> CommandResult:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.dim", justify="right")
    table.add_column(style="seed.text")
    table.add_row("Active provider", provider_label(ctx.config.provider))
    table.add_row("Model", ctx.config.model or "(none — run /model)")
    table.add_row("", "")
    # Every provider keeps its own key + model; switching never loses them.
    for provider in PROVIDERS.values():
        entry = ctx.config.providers.get(provider.id)
        saved_model = entry.model if entry else ""
        if provider.requires_key:
            table.add_row(f"{provider.label} key", ctx.config.masked_key(provider.id))
        table.add_row(f"{provider.label} model", saved_model or "(none)")
    table.add_row("", "")
    table.add_row("Ollama host", ctx.config.ollama_host)
    table.add_row("Theme", ctx.config.theme)
    table.add_row("Username", ctx.config.username)
    table.add_row("Streaming", "on" if ctx.config.stream else "off")
    table.add_row("Max tokens", str(ctx.config.max_tokens))
    ctx.ui.panel(table, title="Configuration")
    return CommandResult()


def apply_setting(ui, config, name: str, raw: str) -> None:
    """Parse, validate, apply, and persist one setting change.

    Global settings first; anything else routes to the ACTIVE provider's own
    settings (e.g. OpenRouter 'mode', FreeModel 'backend', Ollama 'host').
    """
    kind = _SETTINGS.get(name)
    if kind is None:
        try:
            provider = get_provider(config.provider)
        except ProviderError:
            provider = None
        if provider is not None and name in provider.extra_settings(config):
            ok, message = provider.set_extra_setting(config, name, raw)
            if ok:
                save_config(config)
                ui.success(message)
            else:
                ui.warning(message)
            return
        known = sorted(_SETTINGS)
        if provider is not None:
            known += sorted(provider.extra_settings(config))
        ui.warning(f"Unknown setting: {name}. Available: {', '.join(known)}")
        return

    value: object = raw
    if kind is bool:
        lowered = raw.lower()
        if lowered not in ("on", "off", "true", "false"):
            ui.warning(f"'{name}' expects on/off.")
            return
        value = lowered in ("on", "true")
    elif kind is int:
        try:
            value = int(raw)
        except ValueError:
            ui.warning(f"'{name}' expects a number.")
            return
        if value < 1:
            ui.warning(f"'{name}' must be at least 1.")
            return

    try:
        setattr(config, name, value)
    except ValidationError:
        ui.warning(f"Invalid value for '{name}': {raw}")
        return
    save_config(config)
    ui.success(f"{name} set to {value}")


def settings_menu(ui, config) -> None:
    """Interactive settings editor used by the main menu's [5] Settings.

    Shows the global settings plus the ACTIVE provider's own settings
    screen — each provider only ever exposes its own options.
    """
    while True:
        table = Table.grid(padding=(0, 3))
        table.add_column(style="seed.dim", justify="right")
        table.add_column(style="seed.text")
        table.add_row("username", config.username)
        table.add_row("stream", "on" if config.stream else "off")
        table.add_row("max_tokens", str(config.max_tokens))
        try:
            provider = get_provider(config.provider)
            extras = provider.extra_settings(config)
        except ProviderError:
            provider, extras = None, {}
        if extras:
            table.add_row("", "")
            table.add_row(f"— {provider.label} —", "")
            for name, value in extras.items():
                table.add_row(name, value)
        ui.panel(table, title="Settings")
        ui.dim("Change one with '<name> <value>' (e.g. max_tokens 2048); blank to go back.")

        raw = read_line("Setting > ")
        if raw is None or not raw:
            return
        parts = raw.split(maxsplit=1)
        if len(parts) < 2:
            ui.warning("Format: <name> <value>")
            continue
        apply_setting(ui, config, parts[0].lower(), parts[1].strip())


@command("settings", "Change a setting. Usage: /settings <name> <value>")
def _settings(ctx: CommandContext, arg: str) -> CommandResult:
    parts = arg.split(maxsplit=1)
    if len(parts) < 2:
        names = ", ".join(sorted(_SETTINGS))
        ctx.ui.info("Usage: /settings <name> <value>")
        ctx.ui.dim(f"Available settings: {names}")
        return CommandResult()
    apply_setting(ctx.ui, ctx.config, parts[0].lower(), parts[1].strip())
    return CommandResult()
