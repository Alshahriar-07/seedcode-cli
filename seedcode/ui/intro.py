"""SeedCode welcome / empty state.

The first screen is an OpenCode-style home instead of a printed banner: a
blocky two-tone wordmark, a quiet tip, and the real composer underneath. Every
line is clipped to the width it is given, and the block art falls back to a
single gradient wordmark on narrow or short terminals.

Rendering is split into two layers:

* :func:`logo_lines` returns prompt_toolkit-compatible styled fragments, so the
  ``seed`` word can carry the green-to-blue brand gradient while ``code`` stays
  bright white;
* :func:`intro_lines` centers the logo, tagline and tip inside the conversation
  area. The composer itself is a real editor owned by :mod:`seedcode.ui.tui`.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from .theme import gradient_fragments

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .state import AppState

__all__ = [
    "BLOCK_MIN_ROWS",
    "BLOCK_MIN_WIDTH",
    "COMPACT_WORDMARK",
    "PLACEHOLDER",
    "TAGLINE",
    "intro_lines",
    "intro_text",
    "logo_lines",
    "tip_for",
    "wordmark_lines",
]

#: A compact 5-row pixel font for the lowercase logo words.
_FONT: dict[str, tuple[str, ...]] = {
    "s": (" ####", "#    ", " ### ", "    #", "#### "),
    "e": ("#####", "#    ", "#### ", "#    ", "#####"),
    "d": ("#### ", "#   #", "#   #", "#   #", "#### "),
    "c": (" ####", "#    ", "#    ", "#    ", " ####"),
    "o": (" ### ", "#   #", "#   #", "#   #", " ### "),
}

_BLOCK = "\u2588"
_BLOCK_ASCII = "#"

TAGLINE = "terminal coding agent"
PLACEHOLDER = 'Ask anything...  "Fix a TODO in the codebase"'
COMPACT_WORDMARK = "SeedCode"

BLOCK_MIN_WIDTH = 44
BLOCK_MIN_ROWS = 13
BLOCK_WIDTH = 23  # two 4-letter rows at 5 cells + 1 cell gap

CONNECT_TIPS = (
    "Tip  Run /connect to add an AI provider and start coding",
    "Tip  Use /connect once, then ask the workspace a question",
)
READY_TIPS = (
    "Tip  Type / for commands, or /theme to change the palette",
    "Tip  Use /models to switch model without leaving the chat",
    "Tip  Press tab to switch agents and ctrl+p for commands",
    "Tip  Ask for a change and watch the files update in place",
)

#: Chosen once per launch so the tip does not flicker on every repaint.
_CONNECT_TIP = random.choice(CONNECT_TIPS)
_READY_TIP = random.choice(READY_TIPS)


def _letter_rows(letter: str, glyph: str) -> tuple[str, ...]:
    return tuple(row.replace("#", glyph) for row in _FONT[letter])


def wordmark_lines(word: str, *, legacy: bool = False) -> list[str]:
    """One lowercase block word, five rows high."""
    glyph = _BLOCK_ASCII if legacy else _BLOCK
    rows = [""] * 5
    for index, letter in enumerate(word.lower()):
        if index:
            rows = [row + " " for row in rows]
        for line_no, letter_row in enumerate(_letter_rows(letter, glyph)):
            rows[line_no] += letter_row
    return rows


def _center(text: str, width: int) -> str:
    text = text if len(text) <= width else text[: max(0, width)]
    return " " * max(0, (width - len(text)) // 2) + text


def _block_line(word: str, width: int, *, word_index: int) -> list[list[tuple[str, str]]]:
    from .theme import active_palette

    palette = active_palette()
    rows = wordmark_lines(word)
    lines: list[list[tuple[str, str]]] = []
    for row in rows:
        left = max(0, (width - len(row)) // 2)
        fragments: list[tuple[str, str]] = [("", " " * left)]
        if word.lower() == "seed":
            fragments.extend(gradient_fragments(row, palette.brand_start, palette.brand_end))
        else:
            fragments.append((f"fg:{palette.text}", row))
        lines.append(fragments)
    return lines


def logo_lines(width: int, *, legacy: bool = False) -> list[list[tuple[str, str]]]:
    """The two-tone block logo (``seed`` gradient, ``code`` white)."""
    from .theme import active_palette

    palette = active_palette()
    width = max(12, int(width or 80))
    if width < BLOCK_MIN_WIDTH:
        left = max(0, (width - len(COMPACT_WORDMARK)) // 2)
        return [
            [("", " " * left)]
            + gradient_fragments(COMPACT_WORDMARK, palette.brand_start, palette.brand_end)
        ]

    seed = _block_line("seed", width, word_index=0)
    code = _block_line("code", width, word_index=1)
    # One blank row between the two words keeps them as one mark, not two.
    return seed + [[("", "")]] + code


def tip_for(configured: bool) -> str:
    """The context-aware tip shown under the wordmark.

    A first run points at the one action that unblocks the session; a
    configured run gets a quiet discovery hint instead of onboarding text.
    """
    if configured:
        return _READY_TIP
    return _CONNECT_TIP


def _tip_line(text: str, width: int) -> list[tuple[str, str]]:
    from .theme import active_palette

    palette = active_palette()
    marker = "  \u25cf  "
    label = "Tip"
    # Strip the legacy "Tip  " prefix if a caller supplied it.
    body = text[3:] if text.startswith("Tip") else text
    if body.lower().startswith("  "):
        body = body.lstrip()
    plain = marker + label + "  " + body
    left = max(0, (width - len(plain)) // 2)
    body = body[: max(0, width - left - len(marker) - len(label) - 2)]
    command = ""
    if "/" in body:
        import re

        match = re.search(r"(/[\w-]+)", body)
        if match is not None:
            command = match.group(1)
    fragments: list[tuple[str, str]] = [
        ("", " " * left),
        (f"bold fg:{palette.highlight}", marker.rstrip()),
        ("", " "),
        (f"bold fg:{palette.highlight}", label),
        ("", "  "),
    ]
    if command and command in body:
        before, after = body.split(command, 1)
        fragments.extend(
            [
                (f"fg:{palette.muted}", before),
                (f"bold fg:{palette.text}", command),
                (f"fg:{palette.muted}", after),
            ]
        )
    else:
        fragments.append((f"fg:{palette.muted}", body))
    return fragments


def intro_lines(
    state: "AppState | None" = None,
    width: int = 80,
    height: int = 24,
    *,
    legacy: bool = False,
) -> list[list[tuple[str, str]]]:
    """The centered home screen for the conversation area.

    ``width``/``height`` are the conversation region's size, not the whole
    terminal. The real composer is placed below this region by the TUI.
    """
    from .theme import active_palette

    palette = active_palette()
    width = max(12, int(width or 80))
    height = max(4, int(height or 24))
    provider = getattr(state, "provider", "") or ""
    model = getattr(state, "model", "") or ""
    configured = bool(provider and model)

    if width >= BLOCK_MIN_WIDTH and height >= BLOCK_MIN_ROWS:
        body: list[list[tuple[str, str]]] = logo_lines(width, legacy=legacy)
    else:
        body = logo_lines(width, legacy=legacy)
        if height < BLOCK_MIN_ROWS:
            # Short terminals get the single-line wordmark instead of a
            # clipped block logo.
            from .theme import active_palette as _palette

            palette = _palette()
            left = max(0, (width - len(COMPACT_WORDMARK)) // 2)
            body = [
                [("", " " * left)]
                + gradient_fragments(COMPACT_WORDMARK, palette.brand_start, palette.brand_end)
            ]

    if height >= 9 and width >= 34:
        body.append([("", "")])
        subtitle = f"{model}  \u00b7  {provider}" if configured else TAGLINE
        left = max(0, (width - len(subtitle)) // 2)
        body.append([("", " " * left), (f"fg:{palette.dim}", subtitle)])

    if height >= 7:
        body.append([("", "")])
        body.append(_tip_line(tip_for(configured), width))

    if len(body) > height:
        body = body[:height]
    pad_top = max(0, (height - len(body)) // 3)
    blank: list[tuple[str, str]] = [("", "")]
    return [list(blank) for _ in range(pad_top)] + body


def intro_text(
    state: "AppState | None" = None,
    width: int = 80,
    height: int = 24,
    *,
    legacy: bool = False,
) -> str:
    """Plain-text rendering of the intro (tests and non-interactive hosts)."""
    return "\n".join(
        "".join(part for _style, part in line)
        for line in intro_lines(state, width, height, legacy=legacy)
    )
