"""The Seed Code startup dashboard (restored reference layout).

This is the richer v6.2.x startup screen, with **one permanent change: the
ASCII logo is gone for good**. Nothing here draws block, pixel or box art for
branding; the brand is normal text.

The panel is a fixed visual grid — the dimensional specification is the visual
source of truth::

    ╭─ Seed Code CLI v6.2.5 ───────────────────────────────────────────────────────────────────────╮
    │                                                                                              │
    │   Seed Code                                │ Seed Code  |  Eagox Studio                      │
    │   AI CODING AGENT                          │ Plant ideas. Grow code.                         │
    │                                            │ Provider   Default                              │
    │                                            │ Model      cohere/north-mini-code:free          │
    │                                            │ Mode       Chat  •  ● Ready                     │
    ╰──────────────────────────────────────────────────────────────────────────────────────────────╯

Fixed anchor points (96-column primary target):

=================  ====================================================
Outer width        96 columns (capped — it never stretches wider)
Content width      92 columns (borders + 1-column padding each side)
Brand block        content column 2  (screen column 5)
Divider            content column 43 (one blank column each side)
Info section       content column 45
=================  ====================================================

The left cell is branding only — the wordmark as plain text, never art. The
right cell holds identity (brand | publisher), the tagline, and then the live
session values: provider, model, and mode with its status on one line.

Every value comes from live application state — nothing is hardcoded and
"Ready" is never faked for an unconfigured session. The ``API Key`` row is
rendered **only** for providers that actually require a key, so Default and
Ollama never show one, and each value is shown exactly once (no footer repeats
it).

Responsive behaviour: the panel keeps its 96-column design width on wide
terminals, shrinks its value clip below that, falls back to a compact one-row
panel under 64 columns, and to plain text lines under 40 — never overflowing,
never breaking its border. Legacy Windows consoles (raster-font cmd.exe) and
streams that cannot encode the glyphs get the ASCII rendering of the same
layout (``+---+`` borders, ``|`` divider, ``o``/``-`` state marks).

The ``You >`` chat prompt is owned by the prompt session in
:mod:`seedcode.app` and stays outside this module.
"""

from __future__ import annotations

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from .. import APP_NAME, TAGLINE, __publisher__, __version__
from ..core.models import AppConfig
from ..core.providers import (
    PROVIDERS,
    provider_label,
    provider_ready,
    provider_requires_key,
)
from ..core.providers.base import STATUS_CONNECTED, STATUS_UNKNOWN
from .layout import supports_unicode

# --- the dimensional grid (content columns are 0-based) ----------------------
PANEL_WIDTH = 96  # outer width; the panel never exceeds this

_BRAND_IDX = 2  # brand block starts here (screen column 5)
_DIVIDER_IDX = 43  # vertical divider, one blank column on each side
_RIGHT_IDX = 45  # the info section starts here

# Info label column: "Provider   " / "Model      " / "Mode       " / "API Key    ".
_LABEL_WIDTH = 11
# Widest model display at the design width (exactly "cohere/north-mini-code:free");
# the info section slides left to keep it whole, and clips only below that.
_MODEL_CLIP = 27
_MIN_CLIP = 8
# The divider never crowds the brand block: 20 columns stay for the wordmark.
_DIVIDER_MIN = 22

# Below this width the full panel no longer fits: compact panel, then text.
_MIN_PANEL_WIDTH = 64
_MIN_WIDTH = 40

# Plain-text branding only. This is the wordmark that replaced the ASCII logo —
# it can be bold and prominent, but it is never drawn as block/pixel art.
_WORDMARK = APP_NAME
_AGENT_LINE = "AI CODING AGENT"
_BULLET = "•"
_LEGACY_BULLET = "*"


def _bullet(layout_legacy: bool) -> str:
    return _LEGACY_BULLET if layout_legacy else _BULLET


# --- dynamic session values (real application state only) -------------------
def _provider_value(config: AppConfig) -> str:
    """Provider display label ('Not configured' before setup)."""
    if provider_ready(config.provider, config):
        return provider_label(config.provider)
    return "Not configured"


def _model_value(config: AppConfig) -> str:
    if not config.model:
        return "no model"
    if config.model == "auto":
        return "Auto"
    return config.model


def _mode_value(config: AppConfig) -> str:
    """The user-facing mode, read from real session state.

    Code Mode sharpens Assist Mode; both are shown verbatim (never the legacy
    Agent/Desktop names). Imported lazily so the dashboard has no import-cycle
    cost at startup, and guarded so a state error can never break the banner.
    """
    try:
        from ..codemode_state import codemode_state

        if codemode_state().enabled:
            return "Code Mode"
    except Exception:
        pass
    return "Assist Mode" if config.agent_mode else "Chat"


def _status_value(config: AppConfig, ready_mark: str, idle_mark: str) -> Text:
    """Connection status from the provider's session cache (no network I/O)."""
    if not config.is_configured():
        return Text("Setup needed", style="seed.dim")
    provider = PROVIDERS.get(config.provider)
    status = provider.status if provider is not None else STATUS_UNKNOWN
    if status == STATUS_CONNECTED:
        return Text(f"{ready_mark} Connected", style="seed.success")
    if status == STATUS_UNKNOWN:
        # Configured but not probed yet — ready to chat, not yet verified.
        return Text(f"{ready_mark} Ready", style="seed.success")
    return Text(f"{idle_mark} {status}", style="seed.dim")


def _requires_key(config: AppConfig) -> bool:
    """Whether the active provider uses an API key (Default and Ollama do not)."""
    return provider_requires_key(config.provider)


def _api_key_value(config: AppConfig) -> Text:
    """The active provider's key state — masked, never the key itself.

    Only rendered for providers that require a key, so no API-key line is ever
    shown for the built-in Default connection or for local Ollama.
    """
    if config.get_api_key().strip():
        return Text(config.masked_key(), style="seed.dim")
    return Text("Not set — /apikey", style="seed.warning")


def _clip(value: str, limit: int) -> str:
    """Ellipsize a *display* value; the configured value is never modified."""
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)] + "…"


# --- sections ----------------------------------------------------------------
def _brand_rows() -> list[Text]:
    """Left cell: the brand as plain text (the logo is permanently gone)."""
    return [
        Text(_WORDMARK, style="bold seed.primary", no_wrap=True),
        Text(_AGENT_LINE, style="seed.dim", no_wrap=True),
    ]


def _identity_line() -> Text:
    """Right cell header: the product and the studio that publishes it."""
    line = Text(no_wrap=True, overflow="crop")
    line.append(APP_NAME, style="seed.primary")
    line.append("  |  ", style="seed.dim")
    line.append(__publisher__, style="seed.dim")
    return line


def _mode_row(
    config: AppConfig, ready_mark: str, idle_mark: str, bullet: str
) -> Text:
    """Mode and its live status on one line (``Chat  •  ● Ready``)."""
    value = Text(_mode_value(config), style="seed.accent")
    value.append(f"  {bullet}  ", style="seed.dim")
    value.append_text(_status_value(config, ready_mark, idle_mark))
    return _labelled("Mode", value)


def _labelled(label: str, value: Text | str, value_style: str = "seed.text") -> Text:
    """One two-column 'Label    value' information row."""
    line = Text(no_wrap=True, overflow="crop")
    line.append(f"{label:<{_LABEL_WIDTH}}", style="seed.dim")
    if isinstance(value, Text):
        line.append_text(value)
    else:
        line.append(value, style=value_style)
    return line


def _info_rows(
    config: AppConfig,
    ready_mark: str,
    idle_mark: str,
    value_clip: int,
    bullet: str,
) -> list[Text]:
    """Right cell rows: identity, tagline, then the live session values."""
    rows = [
        _identity_line(),
        Text(TAGLINE, style="seed.dim", no_wrap=True, overflow="crop"),
        _labelled(
            "Provider", _clip(_provider_value(config), value_clip), "seed.primary"
        ),
        _labelled("Model", _clip(_model_value(config), value_clip)),
        _mode_row(config, ready_mark, idle_mark, bullet),
    ]
    if _requires_key(config):
        rows.append(_labelled("API Key", _api_key_value(config)))
    return rows


# --- the grid ----------------------------------------------------------------
def _layout(content: int) -> tuple[int, int, int]:
    """``(divider, right_index, value_clip)`` for a given content width.

    The info section is sized around the *whole* model value: it slides left
    from its design anchor (never crowding the brand block) until label, value
    and a trailing blank column fit inside the border, and only a terminal too
    tight even for that starts clipping the value.
    """
    right = min(
        _RIGHT_IDX, max(content - _LABEL_WIDTH - _MODEL_CLIP - 1, _DIVIDER_MIN + 2)
    )
    clip = min(_MODEL_CLIP, max(_MIN_CLIP, content - right - _LABEL_WIDTH - 1))
    return right - 2, right, clip


def _compose(
    left: Text | None,
    right: Text | None,
    *,
    content: int,
    divider: int,
    right_index: int,
    layout_legacy: bool,
) -> Text:
    """One panel row: brand cell, divider, info cell, padded to ``content``."""
    divider_char = "|" if layout_legacy else "│"
    line = Text(no_wrap=True, overflow="crop")

    if left is not None:
        room = max(divider - 1 - _BRAND_IDX, 0)
        left.truncate(room, overflow="ellipsis")
        line.append(" " * _BRAND_IDX)
        line.append_text(left)

    # The divider is always drawn, so the two sections read as one grid.
    pad = divider - line.cell_len
    if pad > 0:
        line.append(" " * pad)
    line.append(divider_char, style="seed.primary")

    if right is not None:
        pad = right_index - line.cell_len
        if pad > 0:
            line.append(" " * pad)
        if line.cell_len < content:
            right.truncate(max(content - line.cell_len, 0), overflow="ellipsis")
            line.append_text(right)

    if line.cell_len < content:
        line.append(" " * (content - line.cell_len))
    line.truncate(content, overflow="crop")
    return line


def _panel_box(layout_legacy: bool):
    return box.ASCII if layout_legacy else box.ROUNDED


def _reference_panel(config: AppConfig, width: int, layout_legacy: bool) -> Panel:
    """The full reference layout, bordered and anchored on the design grid."""
    panel_width = min(width, PANEL_WIDTH)
    content = panel_width - 4  # border (2) + 1-column padding each side
    divider, right_index, value_clip = _layout(content)
    ready_mark = "o" if layout_legacy else "●"
    idle_mark = "-" if layout_legacy else "○"

    brand = _brand_rows()
    info = _info_rows(
        config, ready_mark, idle_mark, value_clip, _bullet(layout_legacy)
    )
    height = max(len(brand), len(info))

    rows: list[Text] = [Text(" " * content)]  # blank row above the content
    for index in range(height):
        rows.append(
            _compose(
                brand[index] if index < len(brand) else None,
                info[index] if index < len(info) else None,
                content=content,
                divider=divider,
                right_index=right_index,
                layout_legacy=layout_legacy,
            )
        )

    title = f"{APP_NAME} CLI v{__version__}"
    title = _clip(title, max(content - 2, 8))
    return Panel(
        Group(*rows),
        title=title,
        title_align="left",
        border_style="seed.primary",
        box=_panel_box(layout_legacy),
        padding=(0, 1),
        expand=False,
        width=panel_width,
    )


# --- compact fallbacks (narrow terminals) ------------------------------------
def _compact_line(config: AppConfig, layout_legacy: bool, width: int) -> Text:
    """The information-dense one-liner used when the panel cannot fit.

    Provider and model give up room first so the mode and the status badge
    always stay readable — the last thing to be clipped is the state.
    """
    content = max(width - 4, 16)  # borders + one padding column each side
    bullet = _bullet(layout_legacy)
    sep = f" {bullet} "
    ready_mark = "o" if layout_legacy else "●"
    idle_mark = "-" if layout_legacy else "○"

    tail = Text()
    tail.append(_mode_value(config), style="seed.accent")
    tail.append(sep, style="seed.dim")
    tail.append_text(_status_value(config, ready_mark, idle_mark))

    line = Text(no_wrap=True, overflow="crop")
    # slack keeps a separation space between clipped values and the tail
    room = content - tail.cell_len - 2 * len(sep) - 4
    if room >= 16:
        each = max(8, room // 2)
        line.append(_clip(_provider_value(config), each), style="seed.primary")
        line.append(sep, style="seed.dim")
        line.append(_clip(_model_value(config), each), style="seed.text")
        line.append(sep, style="seed.dim")
    else:
        line.append(
            _clip(_provider_value(config), max(6, room)), style="seed.primary"
        )
        line.append(sep, style="seed.dim")
    line.append_text(tail)
    line.truncate(content, overflow="crop")
    return line


def _compact_panel(config: AppConfig, width: int, layout_legacy: bool) -> Panel:
    """Three-line banner: top border, one status row, bottom border."""
    line = _compact_line(config, layout_legacy, width)
    title = _clip(f"{APP_NAME} CLI v{__version__}", max(width - 6, 8))
    return Panel(
        line,
        title=title,
        title_align="left",
        border_style="seed.primary",
        box=_panel_box(layout_legacy),
        padding=(0, 1),
        expand=False,
        width=width,
    )


def _plain_header(config: AppConfig, layout_legacy: bool, width: int) -> list[Text]:
    """Oldest fallback: two plain lines for terminals narrower than 40 columns."""
    brand = Text(no_wrap=True, overflow="crop")
    brand.append(_WORDMARK, style="bold seed.primary")
    brand.append("  ", style="seed.dim")
    brand.append(f"v{__version__}", style="seed.dim")
    brand.truncate(width, overflow="crop")
    status = _compact_line(config, layout_legacy, width)
    status.truncate(width, overflow="crop")
    return [brand, status]


def console_size(console: Console) -> int:
    """Current terminal width (80 when unknown, e.g. tests/CI)."""
    return console.size.width or 80


def status_line(console: Console, config: AppConfig) -> Text:
    """The one-line session summary (provider · model · mode · status).

    Shared with mode switches so the running session always has a compact way
    to restate where it is without reprinting the dashboard.
    """
    layout_legacy = not supports_unicode(console)
    return _compact_line(config, layout_legacy, console_size(console))


def render_dashboard(console: Console, config: AppConfig) -> None:
    """Render the startup dashboard to ``console`` (once, at launch)."""
    layout_legacy = not supports_unicode(console)
    width = console_size(console)

    if width >= _MIN_PANEL_WIDTH:
        console.print(_reference_panel(config, width, layout_legacy))
        return
    if width >= _MIN_WIDTH:
        console.print(_compact_panel(config, width, layout_legacy))
        return
    for line in _plain_header(config, layout_legacy, width):
        console.print(line)
