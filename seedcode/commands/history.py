"""Data-inspection and settings commands: /history, /config, /settings."""

from __future__ import annotations

from pydantic import ValidationError
from rich.table import Table

from ..config import save_config
from ..core.providers import PROVIDERS, provider_label
from ..memory import list_sessions
from . import CommandContext, CommandResult, command

# Settings editable via /settings, with a tiny parser per value type.
# (provider/model have their own dedicated commands with live validation.)
_SETTINGS = {
    "username": str,
    "stream": bool,
    "ollama_host": str,
    "max_tokens": int,
}


@command("history", "List saved conversation sessions")
def _history(ctx: CommandContext, arg: str) -> CommandResult:
    sessions = list_sessions()
    if not sessions:
        ctx.ui.dim("No saved sessions yet.")
        return CommandResult()
    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.accent")
    table.add_column(style="seed.dim")
    for sid, count in sessions[:20]:
        table.add_row(sid, f"{count} messages")
    ctx.ui.panel(table, title="History")
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


@command("settings", "Change a setting. Usage: /settings <name> <value>")
def _settings(ctx: CommandContext, arg: str) -> CommandResult:
    parts = arg.split(maxsplit=1)
    if len(parts) < 2:
        names = ", ".join(sorted(_SETTINGS))
        ctx.ui.info("Usage: /settings <name> <value>")
        ctx.ui.dim(f"Available settings: {names}")
        return CommandResult()

    name, raw = parts[0].lower(), parts[1].strip()
    kind = _SETTINGS.get(name)
    if kind is None:
        ctx.ui.warning(f"Unknown setting: {name}. Available: {', '.join(sorted(_SETTINGS))}")
        return CommandResult()

    value: object = raw
    if kind is bool:
        lowered = raw.lower()
        if lowered not in ("on", "off", "true", "false"):
            ctx.ui.warning(f"'{name}' expects on/off.")
            return CommandResult()
        value = lowered in ("on", "true")
    elif kind is int:
        try:
            value = int(raw)
        except ValueError:
            ctx.ui.warning(f"'{name}' expects a number.")
            return CommandResult()
        if value < 1:
            ctx.ui.warning(f"'{name}' must be at least 1.")
            return CommandResult()

    try:
        setattr(ctx.config, name, value)
    except ValidationError:
        ctx.ui.warning(f"Invalid value for '{name}': {raw}")
        return CommandResult()
    save_config(ctx.config)
    ctx.ui.success(f"{name} set to {value}")
    return CommandResult()
