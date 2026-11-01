"""Compact responsive header for the persistent TUI (v9.2.0).

v9.1.0 framed a huge ASCII logo inside the fixed header; v9.2.0 reclaims the
vertical space: the header is now a **compact two-row bar** with no box art
and no block logo — the big logo lives only on the startup dashboard — while
branding stays present as the wordmark:

* row 1 — the identity line: ``Seed Code CLI v9.2.0 · Provider · Model ·
  Mode · Status`` (bold brand, dim separators, live status fragment);
* row 2 (only when the data exists and the bar fits) — the compact status
  bar: ``Workspace … · Context … · API Key …``.

Adaptation to the terminal width is retained:

* **wide** (>= 40 columns) — the two-row compact bar;
* **narrow** (< 40) — a single line with only the essentials.

Two invariants hold at every width: **no line is ever wider than the terminal**
(every value is truncated before it reaches the frame) and **no value is
invented** (an unavailable value is simply omitted, never replaced by filler).

The output is a list of *lines*, each a list of ``(style, text)`` fragments —
the same shape prompt_toolkit consumes and the same shape the tests assert on.
"""

from __future__ import annotations

from .. import APP_NAME, __version__
from .dashboard import LOGO_LINES  # the startup dashboard keeps the big logo
from .state import AppState, status_fragment

__all__ = [
    "LOGO_LINES",
    "MAX_PANEL_WIDTH",
    "header_lines",
    "header_text",
]

_PLAIN_WIDTH = 40
#: Below this even mode/status drop off the single narrow line.
_MODE_MIN_WIDTH = 24
#: The status row only shows in the two-row (>= 40 columns) layout; below
#: that its values would be three clipped fragments of noise.
_BAR_STATUS_MIN_WIDTH = _PLAIN_WIDTH
#: Kept for the legacy console bound (the compact bar never exceeds it
#: either — a bar capped at the terminal width is always <= 96 cells).
MAX_PANEL_WIDTH = 96

_TEST = " \u00b7 "
_ASCII_TEST = "  -  "
_BRAND = f"{APP_NAME} CLI v{__version__}"
_WORDMARK = APP_NAME.upper()  # SEED CODE (legacy-console fallback only)


def _color(role: str) -> str:
    from .theme import active_palette

    return getattr(active_palette(), role, active_palette().text)


def _role(role: str, *, bold: bool = False) -> str:
    color = _color(role)
    return f"bold fg:{color}" if bold else f"fg:{color}"


def _dim() -> str:
    return f"fg:{_color('dim')}"


def _clip(value: str, limit: int) -> str:
    """Truncate a display value to ``limit`` cells (ellipsis on overflow)."""
    text = " ".join(str(value).split())
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return "\u2026"
    return text[: limit - 1] + "\u2026"


def _len(fragments: list[tuple[str, str]]) -> int:
    return sum(len(text) for _, text in fragments)


def _hard_clip(fragments: list[tuple[str, str]], width: int) -> list[tuple[str, str]]:
    """Never let a rendered line exceed ``width`` cells."""
    out: list[tuple[str, str]] = []
    used = 0
    for style, text in fragments:
        if used >= width:
            break
        room = width - used
        if len(text) > room:
            text = text[:room]
        if text:
            out.append((style, text))
            used += len(text)
    return out


def _join(test: str, groups: list[list[tuple[str, str]]]) -> list[tuple[str, str]]:
    """Join fragment groups with the separator ``test`` (empty groups dropped)."""
    out: list[tuple[str, str]] = []
    for group in groups:
        group = [frag for frag in group if frag[1]]
        if not group:
            continue
        if out:
            out.append((_dim(), test))
        out.extend(group)
    return out


def _seg(style: str, value: str) -> tuple[str, str]:
    return (style, value)


# --- value formatting (real state only; empty when unavailable) ---------------
def _provider(state: AppState) -> str:
    return state.provider or "Not configured"


def _model(state: AppState) -> str:
    return state.model or "no model"


def _context(state: AppState) -> str:
    return f"{state.context_limit:,}" if state.context_limit > 0 else ""


def _key(state: AppState) -> str:
    return (state.api_key or "not set") if state.key_required else ""


def _status_cell(state: AppState, legacy: bool) -> list[tuple[str, str]]:
    style, text = status_fragment(state.status, legacy=legacy)
    return [(style, text)]


def _brand(legacy: bool) -> str:
    """The one-line branding: the wordmark, smaller than the framed logo."""
    return _WORDMARK + f" CLI v{__version__}" if legacy else _BRAND


def _identity_fragments(state: AppState, legacy: bool, *, detail: bool = True) -> list[tuple[str, str]]:
    """``Seed Code CLI v9.2.0 · Provider · Model · Mode``.

    The live status is deliberately NOT on this row: the branding line must
    not churn while the agent works (it re-renders every frame). ``detail=``
    False (narrow widths) renders only the brand; provider/model/mode move
    to the second line.
    """
    test = _ASCII_TEST if legacy else _TEST
    groups: list[list[tuple[str, str]]] = [
        [(_role("primary", bold=True), _brand(legacy))],
    ]
    if detail:
        groups.append([(_seg(_role("primary"), _provider(state)))])
        groups.append([(_seg("", _model(state)))])
        if state.mode:
            groups.append([_seg(_role("accent"), state.mode)])
    return _join(test, groups)


def _status_bar_fragments(state: AppState, legacy: bool) -> list[tuple[str, str]]:
    """``Status ... · Workspace ... · Context ... · API Key ...`` (real values only).

    The status leads the bar so it survives even when the rest clips.
    """
    test = _ASCII_TEST if legacy else _TEST
    groups: list[list[tuple[str, str]]] = [_status_cell(state, legacy)]
    if state.workspace:
        groups.append([_seg("", f"Workspace {_clip(state.workspace, 60)}")])
    context = _context(state)
    if context:
        groups.append([_seg("", f"Context {context} tokens")])
    key = _key(state)
    if key:
        groups.append([_seg("", f"API Key {key}")])
    return _join(test, groups)


# --- public API ---------------------------------------------------------------
def header_lines(state: AppState, width: int, *, ascii_only: bool = False) -> list[list[tuple[str, str]]]:
    """Render the compact header for ``state`` at ``width`` columns."""
    width = max(1, int(width or 80))

    identity = _hard_clip(
        _identity_fragments(state, ascii_only, detail=width >= _PLAIN_WIDTH), width
    )
    lines = [identity]
    if width < _PLAIN_WIDTH:
        # Narrow (but >= 24): the second line carries status/provider/model/mode
        # so the essentials never fight the truncated brand for cells.
        if width >= _MODE_MIN_WIDTH:
            lines.append(_hard_clip(_status_line(state, ascii_only), width))
        return [_hard_clip(line, width) for line in lines]

    # Two-row compact bar: churn-free branding above, live status bar below.
    bar = _hard_clip(_status_bar_fragments(state, ascii_only), width)
    lines.append(bar)
    return [_hard_clip(line, width) for line in lines]


def _status_line(state: AppState, legacy: bool) -> list[tuple[str, str]]:
    """The narrow second line: ``Status · Provider · Model · Mode``."""
    test = _ASCII_TEST if legacy else _TEST
    groups: list[list[tuple[str, str]]] = [
        _status_cell(state, legacy),
        [(_role("primary"), _clip(_provider(state), 16))],
        [(_seg("", _model(state)))],
    ]
    if state.mode:
        groups.append([_seg(_role("accent"), state.mode)])
    return _join(test, groups)


def header_text(state: AppState, width: int, *, ascii_only: bool = False) -> str:
    """The header as plain text (tests and non-interactive hosts)."""
    lines = header_lines(state, width, ascii_only=ascii_only)
    return "\n".join("".join(text for _, text in line) for line in lines)
