"""The Seed Code startup dashboard (v8.1.0).

The primary startup branding is the Seed Code ANSI wordmark logo, followed by
a compact, information-dense block with the live session state::

    ╭─ Seed Code CLI v8.1.0 ───────────────────────────────────────────────────────╮
    │   ▄█████ ▄▄▄▄▄ ▄▄▄▄▄ ▄▄▄▄    ▄█████  ▄▄▄  ▄▄▄▄  ▄▄▄▄▄   ▄█████ ██     ██     │
    │   ▀▀▀▄▄▄ ██▄▄  ██▄▄  ██▀██   ██     ██▀██ ██▀██ ██▄▄    ██     ██     ██     │
    │   █████▀ ██▄▄▄ ██▄▄▄ ████▀   ▀█████ ▀███▀ ████▀ ██▄▄▄   ▀█████ ██████ ██     │
    │                                                                              │
    │   Seed Code CLI v8.1.0                                                       │
    │   Plant ideas. Grow code.                                                    │
    │   Provider   OpenRouter                                                      │
    │   Model      gpt-5.1-codex                                                   │
    │   Mode       Code Mode                                                       │
    │   Status     ● Ready                                                         │
    ╰──────────────────────────────────────────────────────────────────────────────╯

The logo is fixed branding (never generated): the exact three ANSI lines below.
Every value under it comes from live application state — nothing is hardcoded
and "Ready" is never faked for an unconfigured session. The ``API Key`` row is
rendered **only** for providers that actually require a key.

Terminal compatibility (v8.1.0):

* Block glyphs need a Unicode-aware console. A raster-font ``cmd.exe`` or a
  redirected stream that cannot encode them gets the same panel with a text
  wordmark instead of the block logo (``+---+``/``|`` fallbacks included), so
  the startup screen always renders.
* The panel never exceeds :data:`PANEL_WIDTH` (96) and no line ever exceeds the
  terminal width: the full panel needs
  :data:`FULL_MIN_WIDTH` columns, below that a one-row compact panel is drawn,
  and below 40 columns plain lines are used.
* Colour is owned by the theme; ``SEEDCODE_PLAIN``/``no_color`` hosts get the
  same layout without colour.
"""

from __future__ import annotations

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from .. import APP_NAME, TAGLINE, __version__
from ..core.models import AppConfig
from ..core.providers import (
    PROVIDERS,
    provider_label,
    provider_ready,
    provider_requires_key,
)
from ..core.providers.base import STATUS_CONNECTED, STATUS_UNKNOWN
from .layout import supports_unicode

# --- the fixed Seed Code logo -------------------------------------------------
#: The exact Seed Code ANSI wordmark. This is branding, not generated art: it
#: is never built from the version or the session state.
LOGO_LINES: tuple[str, ...] = (
    "▄█████ ▄▄▄▄▄ ▄▄▄▄▄ ▄▄▄▄    ▄█████  ▄▄▄  ▄▄▄▄  ▄▄▄▄▄   ▄█████ ██     ██",
    "▀▀▀▄▄▄ ██▄▄  ██▄▄  ██▀██   ██     ██▀██ ██▀██ ██▄▄    ██     ██     ██",
    "█████▀ ██▄▄▄ ██▄▄▄ ████▀   ▀█████ ▀███▀ ████▀ ██▄▄▄   ▀█████ ██████ ██",
)
#: Width of the logo in terminal cells (all glyphs are single-width).
LOGO_WIDTH = max(len(line) for line in LOGO_LINES)

# --- the panel grid -----------------------------------------------------------
PANEL_WIDTH = 96  # outer width; the panel never exceeds this
#: Below this the block logo cannot fit: fall back to the text wordmark.
FULL_MIN_WIDTH = LOGO_WIDTH + 8
#: Below this the full panel no longer fits: compact panel, then text.
_MIN_PANEL_WIDTH = 64
_MIN_WIDTH = 40

_BRAND_INDENT = "   "  # 3 spaces so the logo is centred-ish under the border
_LABEL_WIDTH = 11  # "Provider   " / "Model      " / "Mode       " / "Status     "

# Plain-text branding fallback (consoles that cannot draw/encode the logo).
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
    """The user-facing mode, read from real session state (v8.1.0).

    Chat is shown without the " Mode" suffix so the compact dashboard keeps
    exactly one ``Mode`` row label (see the design invariant asserted in
    ``tests/test_dashboard.py``); the agentic modes keep their full name so the
    session's capability is unambiguous at a glance.
    """
    from ..core.modes import Mode, active_mode, mode_title

    mode = active_mode(config)
    return "Chat" if mode is Mode.CHAT else mode_title(mode)


def _status_value(config: AppConfig, ready_mark: str, idle_mark: str) -> Text:
    """Connection status from the provider's session cache (no network I/O)."""
    if not config.is_configured():
        return Text("Setup needed", style="seed.dim")
    provider = PROVIDERS.get(config.provider)
    status = provider.status if provider is not None else STATUS_UNKNOWN
    if status == STATUS_CONNECTED:
        return Text(f"{ready_mark} Connected", style="seed.success")
    if status == STATUS_UNKNOWN:
        return Text(f"{ready_mark} Ready", style="seed.success")
    return Text(f"{idle_mark} {status}", style="seed.dim")


def _requires_key(config: AppConfig) -> bool:
    """Whether the active provider uses an API key (Default and Ollama do not)."""
    return provider_requires_key(config.provider)


def _api_key_value(config: AppConfig) -> Text:
    """The active provider's key state — masked, never the key itself."""
    if config.get_api_key().strip():
        return Text(config.masked_key(), style="seed.dim")
    return Text("Not set — /apikey", style="seed.warning")


def _clip(value: str, limit: int) -> str:
    """Ellipsize a *display* value; the configured value is never modified."""
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)] + "…"


# --- sections ----------------------------------------------------------------
def _labelled(label: str, value: Text | str, value_style: str = "seed.text") -> Text:
    """One two-column 'Label      value' information row."""
    line = Text(no_wrap=True, overflow="crop")
    line.append(f"{label:<{_LABEL_WIDTH}}", style="seed.dim")
    if isinstance(value, Text):
        line.append_text(value)
    else:
        line.append(value, style=value_style)
    return line


def _logo_rows(use_logo: bool) -> list[Text]:
    """The brand block: the exact logo, or the text wordmark as a fallback.

    ``use_logo`` is True only when the console can draw the block glyphs AND
    the panel is wide enough to hold them; otherwise the plain wordmark is
    used so the startup screen can never overflow or render mojibake.
    """
    if not use_logo:
        return [
            Text(f"{_BRAND_INDENT}{_WORDMARK}", style="bold seed.primary", no_wrap=True),
            Text(f"{_BRAND_INDENT}{_AGENT_LINE}", style="seed.dim", no_wrap=True),
        ]
    return [
        Text(f"{_BRAND_INDENT}{line}", style="seed.primary", no_wrap=True)
        for line in LOGO_LINES
    ]


def _info_rows(config: AppConfig, ready_mark: str, idle_mark: str, clip: int) -> list[Text]:
    """Identity line, tagline, then the live session values."""
    rows = [
        Text(f"{APP_NAME} CLI v{__version__}", style="bold seed.text", no_wrap=True),
        Text(TAGLINE, style="seed.dim", no_wrap=True, overflow="crop"),
        _labelled("Provider", _clip(_provider_value(config), clip), "seed.primary"),
        _labelled("Model", _clip(_model_value(config), clip)),
        _labelled("Mode", _clip(_mode_value(config), clip), "seed.accent"),
    ]
    rows.append(_labelled("Status", _status_value(config, ready_mark, idle_mark)))
    if _requires_key(config):
        rows.append(_labelled("API Key", _api_key_value(config)))
    return rows


# --- the full panel ----------------------------------------------------------
def _reference_panel(config: AppConfig, width: int, layout_legacy: bool) -> Panel:
    """The logo-first panel (or its text-wordmark fallback on legacy consoles)."""
    panel_width = min(width, PANEL_WIDTH)
    content = panel_width - 4  # border (2) + 1-column padding each side
    ready_mark = "o" if layout_legacy else "●"
    idle_mark = "-" if layout_legacy else "○"
    # The block logo is primary branding, but only where it can actually be
    # drawn and fit; narrower/legacy consoles get the wordmark fallback.
    use_logo = not layout_legacy and panel_width >= FULL_MIN_WIDTH

    rows: list[Text] = list(_logo_rows(use_logo))
    rows.append(Text(" " * max(content, 1)))  # breathing room under the logo
    # The clip accounts for the label column and one trailing blank column, so
    # the ellipsis added by _clip survives the panel's own width crop.
    clip = max(content - _LABEL_WIDTH - 1, 8)
    rows.extend(_info_rows(config, ready_mark, idle_mark, clip))

    title = f"{APP_NAME} CLI v{__version__}"
    title = _clip(title, max(content - 2, 8))
    return Panel(
        Group(*rows),
        title=title,
        title_align="left",
        border_style="seed.primary",
        box=box.ASCII if layout_legacy else box.ROUNDED,
        padding=(0, 1),
        expand=False,
        width=panel_width,
    )


# --- compact fallbacks (narrow terminals) ------------------------------------
def _compact_line(config: AppConfig, layout_legacy: bool, width: int) -> Text:
    """The information-dense one-liner used when the full panel cannot fit."""
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
    room = content - tail.cell_len - 2 * len(sep) - 4
    if room >= 16:
        each = max(8, room // 2)
        line.append(_clip(_provider_value(config), each), style="seed.primary")
        line.append(sep, style="seed.dim")
        line.append(_clip(_model_value(config), each), style="seed.text")
        line.append(sep, style="seed.dim")
    else:
        line.append(_clip(_provider_value(config), max(6, room)), style="seed.primary")
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
        box=box.ASCII if layout_legacy else box.ROUNDED,
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
        # The block logo needs both room and a console that can draw it. The
        # text-wordmark fallback keeps a structured panel everywhere else.
        console.print(_reference_panel(config, width, layout_legacy))
        return
    if width >= _MIN_WIDTH:
        console.print(_compact_panel(config, width, layout_legacy))
        return
    for line in _plain_header(config, layout_legacy, width):
        console.print(line)
