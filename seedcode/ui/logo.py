"""Terminal rendering of the official Seed Code mark.

Rich terminals cannot display a PNG inline, so the official artwork in
:mod:`seedcode.branding` (the same pixels ``assets/logo.png`` is built
from) is drawn here with Unicode half-block glyphs: two pixel rows per
terminal cell, keeping the square aspect and the exact Seed Green palette
of the canonical asset. A compact 8x5 nearest-neighbour raster serves the
fixed 88-column dashboard grid; legacy Windows consoles (raster-font
cmd.exe) get a pure-ASCII raster of the same grid — no Unicode, no broken
borders, same brand. Everything is precomputed and cached, so startup
cost is a single in-memory render (no file I/O, no heavy dependencies).
"""

from __future__ import annotations

from functools import lru_cache

from rich.text import Text

from ..branding import ART, CLEAR, GRID, INK, logo_pixels

# Raster the mark 1:1 from its native 16x16 grid: 16 terminal columns and
# 6 rows (two art rows per cell). Any resampling would blur the pixel art;
# 1:1 keeps the official silhouette. Art rows 0-1 are blank tile cap and
# rows 10-11 are the 2-px stem segment — the leaf tip (cols 6-8) sits
# directly above the seed top (cols 6-8), so skipping the stem loses
# nothing visible while keeping the icon 6 terminal rows tall.
RASTER_SIZE = 16
_LOGO_SOURCE_ROWS = (2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15)

# Compact raster for the 88-column dashboard grid: 8 terminal columns x 5
# rows, nearest-neighbour downsample of the same art rows, ink only.
_COMPACT_COLUMNS = 8
_COMPACT_ROWS = 5

_UPPER, _LOWER = "▀", "▄"
_INK_VALUES = frozenset(INK.values())


def _hex(px: tuple[int, int, int, int]) -> str:
    return f"#{px[0]:02x}{px[1]:02x}{px[2]:02x}"


@lru_cache(maxsize=4)
def _pixel_logo_lines(size: int) -> tuple[Text, ...]:
    """The official mark as half-block pixels (two art rows per cell)."""
    grid = logo_pixels(size)
    lines: list[Text] = []
    rows = _LOGO_SOURCE_ROWS if size == GRID else tuple(range(size))
    for i in range(0, len(rows), 2):
        line = Text(no_wrap=True)
        top_row, bottom_row = grid[rows[i]], grid[rows[i + 1]]
        for x in range(size):
            top, bottom = top_row[x], bottom_row[x]
            if top == CLEAR and bottom == CLEAR:
                line.append(" ")
            elif bottom == CLEAR:
                line.append(_UPPER, style=_hex(top))
            elif top == CLEAR:
                line.append(_LOWER, style=_hex(bottom))
            else:
                line.append(_UPPER, style=f"{_hex(top)} on {_hex(bottom)}")
        lines.append(line)
    return tuple(lines)


def _ink_in(
    grid: list[list[tuple[int, int, int, int]]], y: int, x0: int, x1: int
) -> str | None:
    """Seed-palette ink colour in one source row across columns x0..x1."""
    for x in range(x0, x1 + 1):
        px = grid[y][x]
        if px in _INK_VALUES:
            return _hex(px)
    return None


@lru_cache(maxsize=1)
def _compact_logo_lines() -> tuple[Text, ...]:
    """Compact half-block raster (8 columns x 5 rows) of the official mark.

    Nearest-neighbour downsample of the native 16x16 grid: each terminal
    cell covers a 2-column x ~2.4-row block of art pixels and keeps the ink
    silhouette; the tile background drops out so the mark stays crisp at
    small size. Same palette, same proportions — never stretched.
    """
    grid = logo_pixels(GRID)
    rows = _LOGO_SOURCE_ROWS
    lines: list[Text] = []
    for cell in range(_COMPACT_ROWS):
        top_i = cell * len(rows) // _COMPACT_ROWS
        bot_i = max(top_i, (cell + 1) * len(rows) // _COMPACT_ROWS - 1)
        line = Text(no_wrap=True)
        for col in range(_COMPACT_COLUMNS):
            x0, x1 = col * 2, col * 2 + 1
            top = _ink_in(grid, rows[top_i], x0, x1)
            bottom = _ink_in(grid, rows[bot_i], x0, x1)
            if top and bottom:
                line.append(_UPPER, style=f"{top} on {bottom}")
            elif top:
                line.append(_UPPER, style=top)
            elif bottom:
                line.append(_LOWER, style=bottom)
            else:
                line.append(" ")
        lines.append(line)
    return tuple(lines)


@lru_cache(maxsize=1)
def _ascii_logo_lines() -> tuple[Text, ...]:
    """Pure-ASCII raster of the same art rows for legacy Windows consoles.

    Downsamples the source rows by two and maps any ink to '#':
    recognisably the official mark, renderable in every console, coloured
    by the active theme's primary style.
    """
    lines: list[Text] = []
    for y in _LOGO_SOURCE_ROWS[::2]:
        row = "".join("#" if ART[y][x] in INK else " " for x in range(0, GRID, 2))
        lines.append(Text(row.rstrip(), style="seed.primary", no_wrap=True))
    return tuple(lines)


def render_logo_lines(legacy: bool = False, compact: bool = False) -> tuple[Text, ...]:
    """The official Seed Code mark as terminal lines (cached).

    ``legacy=True`` returns the ASCII raster for consoles that cannot show
    half-block glyphs. ``compact=True`` returns the 8x5 raster for the
    88-column dashboard grid; otherwise the full 16x6 half-block raster.
    """
    if legacy:
        return _ascii_logo_lines()
    return _compact_logo_lines() if compact else _pixel_logo_lines(RASTER_SIZE)
