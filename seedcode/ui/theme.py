"""Seed Code theme system.

Multiple named palettes share one branding contract: a primary tone, an
accent, dim/text/warning/error/success roles, and a cursor-row background
for the interactive selector. The default "seed" palette is the classic
Seed Green identity; every other theme keeps the same structure so all
components render correctly under any of them.

Both rendering stacks read from here:

* Rich  — :func:`rich_theme` builds a :class:`rich.theme.Theme` with the
  ``seed.*`` style names used across the app.
* prompt_toolkit — :func:`pt_style` builds the ``sel.*`` style classes used
  by the interactive components in :mod:`seedcode.ui.selector` and friends.

The active theme is module-level state set from config at startup and by
the theme picker; components query it at render time so a theme switch is
instant everywhere.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace

from prompt_toolkit.styles import Style
from rich.theme import Theme

#: Default canvas. Palettes may override it (the light theme does); every
#: surface inherits the active palette's background unless it opts into panel.
CANVAS = "#0A0A0A"

#: Brand gradient, used by the welcome logo and the composer rail.
BRAND_START = "#22C55E"
BRAND_END = "#3B82F6"


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(channel))) for channel in rgb)


def mix(start: str, end: str, amount: float) -> str:
    """Interpolate two hex colours (``amount`` 0..1) and return ``#RRGGBB``."""
    amount = max(0.0, min(1.0, float(amount)))
    a, b = _rgb(start), _rgb(end)
    return _hex(tuple(round(a[i] + (b[i] - a[i]) * amount) for i in range(3)))


def gradient_colors(start: str, end: str, count: int) -> list[str]:
    """One interpolated colour per item in a left-to-right gradient."""
    count = max(1, int(count))
    if count == 1:
        return [start]
    return [mix(start, end, index / (count - 1)) for index in range(count)]


def gradient_fragments(text: str, start: str, end: str) -> list[tuple[str, str]]:
    """Per-character ``(fg style, character)`` fragments for prompt_toolkit/Rich.

    Prompt_toolkit and Rich both negotiate truecolour down to the terminal's
    real colour depth, so this is safe on 256-colour Windows consoles too.
    """
    colours = gradient_colors(start, end, len(text))
    return [(f"fg:{colour}", char) for char, colour in zip(text, colours) if char]


def _xterm256(rgb: tuple[int, int, int]) -> int:
    """Nearest xterm-256 index for an RGB triple."""
    levels = (0, 95, 135, 175, 215, 255)
    indices = [min(range(6), key=lambda i: abs(levels[i] - channel)) for channel in rgb]
    return 16 + 36 * indices[0] + 6 * indices[1] + indices[2]


def gradient(text: str, start: str = BRAND_START, end: str = BRAND_END) -> str:
    """ANSI-gradient ``text`` character by character (truecolour when available)."""
    colours = gradient_colors(start, end, len(text))
    truecolor = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
    out: list[str] = []
    for char, colour in zip(text, colours):
        if char.isspace():
            out.append(char)
            continue
        if truecolor:
            r, g, b = _rgb(colour)
            out.append(f"\x1b[38;2;{r};{g};{b}m{char}\x1b[0m")
        else:
            out.append(f"\x1b[38;5;{_xterm256(_rgb(colour))}m{char}\x1b[0m")
    return "".join(out)


def shimmer_fragments(text: str, base: str, highlight: str, phase: float) -> list[tuple[str, str]]:
    """A moving highlight across ``text`` for the live activity line."""
    out: list[tuple[str, str]] = []
    length = max(1, len(text))
    for index, char in enumerate(text):
        wave = 0.5 + 0.5 * math.sin((index / length) * math.tau - phase)
        out.append((f"fg:{mix(base, highlight, wave * 0.8)}", char))
    return out


@dataclass(frozen=True)
class Palette:
    """One named colour palette (all values are hex strings)."""

    id: str
    label: str
    description: str
    primary: str
    accent: str
    text: str
    dim: str
    warning: str
    error: str
    success: str
    cursor_bg: str  # selector highlighted-row background
    # Shared visual roles. Defaults keep every existing palette valid while
    # the new interface gains the canvas, surface, highlight and brand roles.
    border: str = "#27272A"
    info: str = "#3B82F6"
    secondary: str = "#16A34A"
    muted: str = "#6B7280"
    bg: str = CANVAS
    surface: str = "#1C1C1C"
    highlight: str = "#FB923C"
    brand_start: str = BRAND_START
    brand_end: str = BRAND_END


PALETTES: dict[str, Palette] = {
    p.id: p
    for p in (
        Palette(
            id="seed",
            label="Seed Green",
            description="The signature Seed Code identity on a soft near-black canvas",
            primary="#22C55E",
            accent="#3B82F6",
            text="#E5E5E5",
            dim="#6B7280",
            warning="#FB923C",
            error="#EF4444",
            success="#22C55E",
            cursor_bg="#14301F",
            border="#2A2A2A",
            info="#3B82F6",
            secondary="#22C55E",
            muted="#6B7280",
            bg="#0A0A0A",
            surface="#1C1C1C",
            highlight="#FB923C",
        ),
        Palette(
            id="forest",
            label="Forest",
            description="Deep greens and moss",
            primary="#27ae60",
            accent="#a3e4b8",
            text="#e8f5e9",
            dim="#7d8f84",
            warning="#e6c229",
            error="#e74c3c",
            success="#27ae60",
            cursor_bg="#16301f",
        ),
        Palette(
            id="ocean",
            label="Ocean",
            description="Calm blues and cyan",
            primary="#3498db",
            accent="#7fd6f2",
            text="#eaf6fb",
            dim="#8496a3",
            warning="#f1c40f",
            error="#e74c3c",
            success="#2ecc71",
            cursor_bg="#14303f",
        ),
        Palette(
            id="dusk",
            label="Dusk",
            description="Violet evening tones",
            primary="#9b59b6",
            accent="#d2a8e0",
            text="#f5eefa",
            dim="#988fa3",
            warning="#f1c40f",
            error="#e74c3c",
            success="#2ecc71",
            cursor_bg="#2d1f38",
        ),
        Palette(
            id="ember",
            label="Ember",
            description="Warm amber and orange",
            primary="#e67e22",
            accent="#f5b971",
            text="#fdf3e7",
            dim="#a3937f",
            warning="#f1c40f",
            error="#e74c3c",
            success="#2ecc71",
            cursor_bg="#3a2712",
        ),
        Palette(
            id="mono",
            label="Monochrome",
            description="Plain white on black",
            primary="#ffffff",
            accent="#c8c8c8",
            text="#ffffff",
            dim="#808080",
            warning="#f1c40f",
            error="#e74c3c",
            success="#ffffff",
            cursor_bg="#333333",
        ),
    )
}

# The requested theme names are first-class palettes; the older palettes stay
# available so existing configs keep working.
PALETTES = {
    "dark-gradient": replace(
        PALETTES["seed"],
        id="dark-gradient",
        label="Dark Gradient",
        description="Near-black canvas with the Seed green-to-blue brand gradient",
    ),
    "dark-solid": replace(
        PALETTES["seed"],
        id="dark-solid",
        label="Dark Solid",
        description="Near-black canvas with flat Seed accents",
        accent="#22C55E",
        secondary="#22C55E",
        cursor_bg="#1F1F1F",
    ),
    "light": replace(
        PALETTES["seed"],
        id="light",
        label="Light",
        description="Paper-white canvas with the same terminal hierarchy",
        primary="#15803D",
        accent="#2563EB",
        text="#18181B",
        dim="#52525B",
        warning="#EA580C",
        error="#DC2626",
        success="#16A34A",
        cursor_bg="#DCFCE7",
        border="#D4D4D8",
        info="#2563EB",
        secondary="#15803D",
        muted="#71717A",
        bg="#FAFAFA",
        surface="#F4F4F5",
        highlight="#EA580C",
    ),
    **PALETTES,
}
DEFAULT_THEME = "dark-gradient"

# Module-level active theme (set from config at startup, and live by the
# theme picker). Components read it at render time.
_active: str = DEFAULT_THEME


def set_active_theme(name: str) -> Palette:
    """Set the active theme (unknown names fall back to the default)."""
    global _active
    _active = name if name in PALETTES else DEFAULT_THEME
    return PALETTES[_active]


def active_theme_name() -> str:
    return _active


def active_palette() -> Palette:
    return PALETTES.get(_active, PALETTES[DEFAULT_THEME])


def rich_theme(name: str | None = None) -> Theme:
    """Rich theme with the ``seed.*`` styles used across the app."""
    p = PALETTES.get(name or _active, PALETTES[DEFAULT_THEME])
    return Theme(
        {
            "": f"{p.text} on {p.bg}",
            "seed.bg": f"on {p.bg}",
            "seed.surface": f"on {p.surface}",
            "seed.highlight": f"bold {p.highlight}",
            "seed.primary": f"bold {p.primary}",
            "seed.accent": p.accent,
            "seed.text": p.text,
            "seed.dim": p.dim,
            "seed.warning": p.warning,
            "seed.error": f"bold {p.error}",
            "seed.success": p.success,
            "seed.secondary": p.secondary,
            "seed.info": p.info,
            "seed.border": p.border,
            "seed.muted": p.muted,
            "seed.prompt": f"bold {p.primary}",
            "seed.assistant": p.accent,
            "markdown.code": p.accent,
        }
    )


def pt_style(name: str | None = None) -> Style:
    """prompt_toolkit style with the ``sel.*`` classes the components use."""
    p = PALETTES.get(name or _active, PALETTES[DEFAULT_THEME])
    return Style.from_dict(
        {
            # v10.2.0: the interface owns a pure black background; every
            # component inherits it unless it sets its own role colour.
            # The root style owns the complete viewport: prompt_toolkit fills
            # every unpainted cell (including unused rows/columns) with it.
            "": f"bg:{p.bg} fg:{p.text}",
            "canvas": f"bg:{p.bg}",
            "surface": f"bg:{p.surface} fg:{p.text}",
            "placeholder": f"bg:{p.surface} fg:{p.muted}",
            "placeholder.cursor": f"bg:{p.surface} fg:{p.text}",
            "prompt": f"bold {p.primary}",
            # Shared by the remaining prompt_toolkit Frame-based selectors.
            "frame.border": p.border,
            "frame.label": f"bold {p.primary}",
            "composer": f"bg:{p.surface}",
            "composer.hint": f"bg:{p.bg} fg:{p.muted}",
            "composer.key": f"bg:{p.bg} fg:{p.text} bold",
            "statusbar": f"bg:{p.bg} fg:{p.muted}",
            "statusbar.strong": f"bg:{p.bg} fg:{p.text}",
            "sel.title": f"bold {p.primary}",
            "sel.breadcrumb": p.dim,
            "sel.breadcrumb.here": f"bold {p.accent}",
            "sel.searchlabel": f"bold {p.primary}",
            "sel.query": p.text,
            "sel.placeholder": f"italic {p.dim}",
            "sel.pointer": f"bold {p.primary}",
            "sel.cursorline": f"bg:{p.cursor_bg}",
            "sel.text": p.text,
            "sel.dim": p.dim,
            "sel.match": f"bold underline {p.accent}",
            "sel.group": f"bold {p.accent}",
            "sel.hint": p.dim,
            "sel.counter": p.dim,
            "sel.ok": p.success,
            "sel.warn": p.warning,
            "sel.err": f"bold {p.error}",
            "sel.off": p.dim,
            "sel.scroll": p.dim,
            "sel.swatch.primary": f"bg:{p.primary}",
            "sel.swatch.accent": f"bg:{p.accent}",
            "sel.swatch.dim": f"bg:{p.dim}",
        }
    )


# Backwards-compatible export: the classic Seed Green Rich theme.
SEED_THEME = rich_theme(DEFAULT_THEME)
