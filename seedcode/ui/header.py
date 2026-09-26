"""Responsive header rendering for the persistent TUI (v8.2.5).

The header is the fixed top region of the interface. It is rendered from the
live :class:`~seedcode.ui.state.AppState` on every frame, so a provider, model,
mode or status change is visible immediately, and it adapts to the terminal
width:

* **wide** (>= :data:`_LOGO_MIN_WIDTH`, 76 columns) — a bordered reference
  panel: the fixed Seed Code **ASCII logo** and tagline, then ``Provider``/
  ``Model``, ``Mode``/``Status`` and (when the data exists) ``Workspace``/
  ``Context`` rows. The branding is never replaced by plain text;
* **medium** (>= 40) — a three-line panel with the status line beside the mode;
* **narrow** (< 40) — one or two plain lines with only the essentials.

The logo is branding, not generated art, and it is never dropped for
cosmetics: below :data:`_LOGO_MIN_WIDTH` the block glyphs genuinely cannot be
drawn without clipping, so the compact layouts carry the same information
without them.

Two invariants hold at every width: **no line is ever wider than the terminal**
(every value is truncated before it reaches the frame) and **no value is
invented** (an unavailable value is simply omitted, never replaced by filler).

The output is a list of *lines*, each a list of ``(style, text)`` fragments —
the same shape prompt_toolkit consumes and the same shape the tests assert on.
"""

from __future__ import annotations

from .. import APP_NAME, TAGLINE, __version__
from .dashboard import LOGO_LINES, LOGO_WIDTH
from .state import AppState, status_fragment

__all__ = ["header_lines", "header_text", "MAX_PANEL_WIDTH"]

_PLAIN_WIDTH = 40
_MIN_WIDTH = 24
MAX_PANEL_WIDTH = 96

_LABEL_WIDTH = 9
_COLUMN_GAP = 3
_WORDMARK = APP_NAME.upper()  # SEED CODE (legacy-console fallback only)

#: The fixed branding, indented exactly like the startup dashboard.
_LOGO_INDENT = "   "
#: Below this the block logo cannot be drawn un-clipped: the compact (metadata
#: only) layouts are used instead. The logo itself is never truncated.
_LOGO_MIN_WIDTH = LOGO_WIDTH + len(_LOGO_INDENT) + 3

_ROUND = {"tl": "\u256d", "tr": "\u256e", "bl": "\u2570", "br": "\u256f", "h": "\u2500", "v": "\u2502", "d": "\u00b7"}
_ASCII = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|", "d": "-"}


def _box(legacy: bool) -> dict[str, str]:
    return _ASCII if legacy else _ROUND


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


def _cell(label: str, value, width: int) -> list[tuple[str, str]]:
    """A ``Label     value`` cell of at most ``width`` cells.

    ``value`` is a string or a styled-fragment list (the status cell).
    """
    if width <= 0:
        return []
    label_text = _clip(label, max(1, min(_LABEL_WIDTH, max(1, width - 1))))
    pad = " " * max(0, _LABEL_WIDTH - len(label_text))
    fragments: list[tuple[str, str]] = [(_dim(), label_text + pad + " ")]
    used = _len(fragments)
    room = max(1, width - used)
    if isinstance(value, list):
        fragments.extend(value)
    else:
        fragments.append(("", _clip(value, room)))
    return _hard_clip(fragments, width)


def _hard_clip(fragments: list[tuple[str, str]], width: int) -> list[tuple[str, str]]:
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


def _pair(
    left: list[tuple[str, str]],
    right: list[tuple[str, str]] | None,
    inner: int,
) -> list[tuple[str, str]]:
    """Left-aligned content with an optional right-aligned cell on one row.

    This is the compact single-line form: the identity line puts ``Provider ·
    Model · Mode`` on the left and the live status on the right, so each value
    is rendered exactly once (there is no second toolbar repeating them).
    """
    inner = max(0, inner)
    right = right or []
    rlen = _len(right)
    if rlen and rlen + 1 < inner:
        left = _hard_clip(left, inner - rlen - 1)
        gap = inner - _len(left) - rlen
        out = list(left)
        if gap > 0:
            out.append(("", " " * gap))
        out.extend(right)
        return _hard_clip(out, inner)
    out = _hard_clip(left, inner)
    pad = inner - _len(out)
    if pad > 0:
        out.append(("", " " * pad))
    return out


def _row2(left: tuple[str, object], right: tuple[str, object] | None, inner: int) -> list[tuple[str, str]]:
    """Two label/value cells laid out across ``inner`` cells."""
    if right is None:
        return _cell(left[0], left[1], inner)
    col = (inner - _COLUMN_GAP) // 2
    fragments = _cell(left[0], left[1], col)
    fragments.append(("", " " * max(0, col - _len(fragments))))
    tail = inner - col - _COLUMN_GAP
    if tail > 0:
        fragments.append(("", " " * _COLUMN_GAP))
        fragments.extend(_cell(right[0], right[1], tail))
    pad = inner - _len(fragments)
    if pad > 0:
        fragments.append(("", " " * pad))
    return _hard_clip(fragments, inner)


def _framed(body: list[tuple[str, str]], inner: int, box: dict[str, str]) -> list[tuple[str, str]]:
    """One ``│ body │`` row: one column of breathing room, padded to ``inner``."""
    bar = _dim()
    padded: list[tuple[str, str]] = [("", " ")] + list(body)
    pad = inner - _len(padded)
    if pad > 0:
        padded.append(("", " " * pad))
    return [(bar, box["v"])] + _hard_clip(padded, inner) + [(bar, box["v"])]


def _border(title: str, width: int, box: dict[str, str], top: bool) -> list[tuple[str, str]]:
    left = box["tl"] if top else box["bl"]
    right = box["tr"] if top else box["br"]
    if not top:
        return [(_dim(), left + box["h"] * max(0, width - 2) + right)]
    fill = max(0, width - 2 - len(title) - 1)
    return [(_dim(), left + box["h"] + title + box["h"] * fill + right)]


# --- value formatting (real state only; empty when unavailable) ---------------
def _provider(state: AppState) -> str:
    return state.provider or "Not configured"


def _model(state: AppState) -> str:
    return state.model or "no model"


def _context(state: AppState) -> str:
    return f"{state.context_limit:,}" if state.context_limit > 0 else ""


def _key(state: AppState) -> str:
    return (state.api_key or "not set") if state.key_required else ""


def _status_cell(state: AppState, legacy: bool) -> tuple[str, list[tuple[str, str]]]:
    style, text = status_fragment(state.status, legacy=legacy)
    return text, [(style, text)]


def _identity_fragments(state: AppState) -> list[tuple[str, str]]:
    """``Provider · Model · Mode`` on one line — the single source of truth."""
    return [
        (_role("primary"), _provider(state)),
        (_dim(), " \u00b7 "),
        ("", _model(state)),
        (_dim(), " \u00b7 "),
        (_role("accent"), state.mode or "Chat"),
    ]


# --- layouts ------------------------------------------------------------------
def _panel_lines(state: AppState, width: int, legacy: bool) -> list[list[tuple[str, str]]]:
    box = _box(legacy)
    panel_w = min(width, MAX_PANEL_WIDTH)
    inner = max(panel_w - 2, 8)
    title = f" {APP_NAME} CLI v{__version__} "

    lines: list[list[tuple[str, str]]] = [_border(title, panel_w, box, True)]
    if legacy:
        # A console that cannot encode the block glyphs still gets branding —
        # the wordmark form — rather than mojibake or an empty header.
        lines.append(
            _framed([(_role("primary", bold=True), _LOGO_INDENT + _WORDMARK)], inner, box)
        )
    else:
        # The exact Seed Code ASCII logo is primary branding. It is written
        # verbatim (never whitespace-collapsed) so the art keeps its shape, and
        # only reaches this layout when it provably fits (see _LOGO_MIN_WIDTH).
        for logo_line in LOGO_LINES:
            lines.append(_framed([(_role("primary"), _LOGO_INDENT + logo_line)], inner, box))
        lines.append(_framed([(_dim(), _LOGO_INDENT + TAGLINE)], inner, box))
    _, status_cell = _status_cell(state, legacy)
    # ``_pair`` fills its row edge to edge; ``_framed`` then adds one leading
    # cell, so the row is built one column narrower to keep the status intact.
    lines.append(
        _framed(_pair(_identity_fragments(state), status_cell, inner - 2), inner, box)
    )

    workspace = state.workspace or ""
    context = _context(state)
    if workspace or context:
        left = ("Workspace", workspace) if workspace else ("", "")
        right = ("Context", context) if context else None
        lines.append(_framed(_row2(left, right, inner), inner, box))

    key = _key(state)
    connection = state.connection or ""
    if key or connection:
        left = ("API Key", key) if key else ("", "")
        right = ("Link", connection) if connection else None
        lines.append(_framed(_row2(left, right, inner), inner, box))

    lines.append(_border(title, panel_w, box, False))
    return lines


def _compact_lines(state: AppState, width: int, legacy: bool) -> list[list[tuple[str, str]]]:
    box = _box(legacy)
    panel_w = max(width, 8)
    inner = max(panel_w - 2, 6)
    title = f" {APP_NAME} CLI v{__version__} "

    lines = [_border(title, panel_w, box, True)]

    _, status_cell = _status_cell(state, legacy)
    head: list[tuple[str, str]] = list(status_cell)
    head.append((_dim(), "  " + box["d"] + "  "))
    room = max(4, inner - _len(head))
    head.append((_role("accent"), _clip(state.mode, room)))
    lines.append(_framed(head, inner, box))

    info: list[tuple[str, str]] = [(_role("primary"), _clip(_provider(state), 20))]
    info.append((_dim(), "  " + box["d"] + "  "))
    room = max(4, inner - _len(info))
    info.append(("", _clip(_model(state), room)))
    lines.append(_framed(info, inner, box))

    lines.append(_border(title, panel_w, box, False))
    return lines


def _plain_lines(state: AppState, width: int, legacy: bool) -> list[list[tuple[str, str]]]:
    _, status_cell = _status_cell(state, legacy)
    brand: list[tuple[str, str]] = [
        (_role("primary", bold=True), APP_NAME),
        (_dim(), f" v{__version__}  "),
    ]
    brand.extend(status_cell)
    lines = [_hard_clip(brand, width)]
    if width >= _MIN_WIDTH:
        info: list[tuple[str, str]] = [(_role("primary"), _clip(_provider(state), 18))]
        info.append((_dim(), "  \u00b7  "))
        room = max(4, width - _len(info))
        info.append(("", _clip(_model(state), room)))
        lines.append(_hard_clip(info, width))
    return lines


# --- public API ---------------------------------------------------------------
def header_lines(state: AppState, width: int, *, ascii_only: bool = False) -> list[list[tuple[str, str]]]:
    """Render the header for ``state`` at ``width`` columns."""
    width = max(1, int(width or 80))
    if width >= _LOGO_MIN_WIDTH:
        lines = _panel_lines(state, width, ascii_only)
    elif width >= _PLAIN_WIDTH:
        lines = _compact_lines(state, width, ascii_only)
    else:
        lines = _plain_lines(state, width, ascii_only)
    return [_hard_clip(line, width) for line in lines]


def header_text(state: AppState, width: int, *, ascii_only: bool = False) -> str:
    """The header as plain text (tests and non-interactive hosts)."""
    lines = header_lines(state, width, ascii_only=ascii_only)
    return "\n".join("".join(text for _, text in line) for line in lines)
