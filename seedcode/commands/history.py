"""Data-inspection and settings commands: /history, /config, /settings."""

from __future__ import annotations

from rich.table import Table

from ..config import save_config
from ..memory import list_sessions
from . import CommandContext, CommandResult, command

# Settings editable via /settings, with a tiny parser per value type.
_SETTINGS = {
    "username": str,
    "stream": bool,
    "model": str,
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
    table.add_row("API Key", ctx.config.masked_key())
    table.add_row("Model", ctx.config.model)
    table.add_row("Provider", ctx.config.provider)
    table.add_row("Theme", ctx.config.theme)
    table.add_row("Username", ctx.config.username)
    table.add_row("Streaming", "on" if ctx.config.stream else "off")
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

    setattr(ctx.config, name, value)
    save_config(ctx.config)
    ctx.ui.success(f"{name} set to {value}")
    return CommandResult()
