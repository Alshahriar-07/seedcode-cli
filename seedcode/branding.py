"""Canonical Seed Code brand artwork — single source of truth.

The official Seed Code mark is the 16x16 pixel-art "sprouting seed" on a
dark rounded tile (the Seed Green identity). This module owns the grid and
the exact palette so every surface renders identical art:

* ``scripts/windows/build_assets.py`` — ``seedcode.ico``, the Inno wizard
  bitmaps, the canonical ``assets/logo.png`` and the preview render
* the installer wizard and the Windows taskbar/Explorer surfaces

v7.2.5 note: this module stays a packaging/desktop asset. The terminal UI
renders its own branding from :data:`seedcode.ui.dashboard.LOGO_LINES` (the
exact ANSI wordmark, with a text fallback on narrow or non-Unicode consoles),
so no terminal surface imports this module and the pixel-art mark never costs
anything at startup.

Stdlib only (struct/zlib): safe to import from anywhere in the package,
costs nothing at startup, and keeps the brand byte-reproducible.
"""

from __future__ import annotations

import struct
import zlib

GRID = 16  # the mark is authored on a 16x16 grid

# --- palette (mirrors seedcode/ui/theme.py) ---------------------------------
ACCENT = (123, 237, 159, 255)  # Soft Green — leaf highlight
PRIMARY = (46, 204, 113, 255)  # Seed Green — leaf body
DARK = (27, 122, 67, 255)      # seed body
TILE_BG = (13, 17, 23, 255)    # dark rounded tile behind the mark
CLEAR = (0, 0, 0, 0)           # outside the rounded tile (transparent)

# --- the mark: a sprouting seed, 16x16 --------------------------------------
ART: list[str] = [
    "................",
    "................",
    "...LL.....GG....",
    "..LLLL...GGGG...",
    ".LLLLLL.GGGGG...",
    ".LLLLLL.GGGGGG..",
    "..LLLLL.GGGGG...",
    "...LLLL.GGGG....",
    ".....LL.GG......",
    "......LGG.......",
    ".......G........",
    ".......G........",
    "......DDD.......",
    ".....DDDDD......",
    ".....DDDDD......",
    "......DDD.......",
]
INK = {"L": ACCENT, "G": PRIMARY, "D": DARK}

Pixel = tuple[int, int, int, int]


def logo_pixels(size: int = GRID) -> list[list[Pixel]]:
    """Render the mark at ``size`` px: rounded dark tile + scaled pixel art."""
    radius = size * 3 // 16
    grid = [[CLEAR] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            # Rounded-rect clip: inside unless outside a corner circle.
            cx = radius if x < radius else (size - 1 - radius if x >= size - radius else x)
            cy = radius if y < radius else (size - 1 - radius if y >= size - radius else y)
            if (x - cx) ** 2 + (y - cy) ** 2 > radius * radius and (
                (x < radius or x >= size - radius) and (y < radius or y >= size - radius)
            ):
                continue
            cell = ART[y * GRID // size][x * GRID // size]
            grid[y][x] = INK.get(cell, TILE_BG)
    return grid


def render_logo_png(size: int = 256) -> bytes:
    """Encode the mark at ``size`` px as an RGBA PNG (the canonical asset)."""
    grid = logo_pixels(size)
    h = w = size

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    raw = b"".join(
        b"\x00" + b"".join(bytes(px) for px in row) for row in grid
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
