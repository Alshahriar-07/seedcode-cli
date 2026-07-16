"""Informational commands: /help, /version."""

from __future__ import annotations

from rich.table import Table

from .. import __version__
from . import CommandContext, CommandResult, _REGISTRY, command


@command("help", "Show available commands")
def _help(ctx: CommandContext, arg: str) -> CommandResult:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="seed.primary", no_wrap=True)
    table.add_column(style="seed.text")
    for name, (_, help_text) in sorted(_REGISTRY.items()):
        table.add_row(f"/{name}", help_text)
    ctx.ui.panel(table, title="Commands")
    return CommandResult()


@command("version", "Show the Seed Code version")
def _version(ctx: CommandContext, arg: str) -> CommandResult:
    ctx.ui.info(f"Seed Code v{__version__}")
    return CommandResult()
