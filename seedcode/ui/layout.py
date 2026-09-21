"""Shared layout conventions: panels, key-value grids, shortcut tables.

Every screen builds its content through these helpers so padding, borders
and column styles stay identical across the app.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# The one decorative mark the v6.2.5 UI allows: a short horizontal rule that
# separates a heading from its values. It is a single dim glyph run (never a
# box, never a full-width banner) and falls back to ASCII hyphens on legacy
# Windows consoles that cannot draw it.
RULE_WIDTH = 44


# Glyphs the UI draws: the rule, the state marks, the separator dot.
UI_GLYPHS = "─●○✓✗■·–↑↓│…"


def supports_unicode(console) -> bool:
    """Whether ``console``'s stream can actually encode the UI's glyphs.

    A raster-font cmd.exe cannot *draw* them (``legacy_windows``), and a
    redirected stream on a cp1252 host cannot even *encode* them — writing
    either would raise mid-render. Both cases fall back to ASCII.
    """
    if getattr(console, "legacy_windows", False):
        return False
    encoding = getattr(getattr(console, "file", None), "encoding", None)
    if not encoding:
        return True  # in-memory/recording consoles accept everything
    try:
        UI_GLYPHS.encode(encoding)
    except (UnicodeEncodeError, LookupError, TypeError):
        return False
    return True


def rule(
    width: int = RULE_WIDTH, *, legacy: bool = False, style: str = "seed.dim"
) -> Text:
    """A short horizontal rule, cropped (never wrapped) on narrow terminals."""
    line = Text(no_wrap=True, overflow="crop")
    line.append("-" * width if legacy else "─" * width, style=style)
    return line


def branded_panel(body: RenderableType, title: str | None = None) -> Panel:
    """The standard Seed Code panel: primary border, left title, padding."""
    return Panel(
        body,
        title=title,
        border_style="seed.primary",
        title_align="left",
        padding=(1, 2),
    )


def kv_grid(rows: Iterable[tuple[str, RenderableType]]) -> Table:
    """A two-column label/value grid (labels dimmed right, values plain)."""
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style="seed.dim", justify="right", no_wrap=True)
    grid.add_column(style="seed.text")
    for label, value in rows:
        grid.add_row(label, value)
    return grid


def columns_grid(rows: Sequence[Sequence[str]], styles: Sequence[str]) -> Table:
    """An n-column grid with one style per column."""
    grid = Table.grid(padding=(0, 2))
    for style in styles:
        grid.add_column(style=style)
    for row in rows:
        grid.add_row(*row)
    return grid


def shortcuts_grid(pairs: Sequence[tuple[str, str]]) -> Table:
    """Keyboard-shortcut table: accent keys, plain descriptions."""
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style="seed.accent", no_wrap=True)
    grid.add_column(style="seed.text")
    for key, action in pairs:
        grid.add_row(key, action)
    return grid
