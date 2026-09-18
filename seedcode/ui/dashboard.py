"""The Seed Code startup dashboard (v6.2.0 dimensional reference layout).

The panel is a fixed visual grid — the dimensional specification is the
visual source of truth::

    ╭─ Seed code v6.2.0 ──────────────────────────────────────────────╮
    │                                                                 │
    │   [logo]   SEEDCODE CLI              │   Seed Code | Eagox ...  │
    │            Plant ideas. Grow code.   │   Plant ideas. Grow ...  │
    │            (logo)                    │   Provider   OpenRouter  │
    │            (logo)                    │   Model      deepseek/…  │
    │            (logo)                    │   Mode       Assist      │
    │                                      │   Status     ● Ready     │
    │                                                                 │
    ╰─────────────────────────────────────────────────────────────────╯
    You >

Fixed anchor points (88-column primary target):

=================  ====================================================
Outer width        88 columns (capped — never full terminal width)
Inner width        86 columns (borders + 1-column padding each side)
Left content       column 5 (logo); brand text column 18
Divider            column 42 (one blank column each side, never touching
                   the outer top/bottom border)
Right content      column 47
Content rows       8 (blank / 6 info rows / blank)
=================  ====================================================

One rounded green box with the version integrated into the top border, the
official mark (see :mod:`seedcode.ui.logo` — the same pixels as
``assets/logo.png``) as a compact square icon on the left, a vertical green
divider, and live session information on the right. Provider, model, mode
and status are read from real application state — nothing here is
hardcoded; long model ids are visually clipped only, the configured value
is never modified. The ``You >`` chat prompt stays outside the panel,
owned by the prompt session in :mod:`seedcode.app`.

Responsive behaviour: at 120+ columns the panel keeps its 88-column design
width; at 88 columns the exact target grid renders; below 88 the value
clip (and, if needed, the right-hand anchor) shrink gracefully — never
overflowing, breaking borders or overlapping the divider. Narrow terminals
and legacy Windows consoles (raster-font cmd.exe) fall back to the compact
one-line banner: same information, same brand, no broken borders. The
panel is 10 terminal rows tall (border + 8 content rows + border) and
renders once at startup.
"""

from __future__ import annotations

from rich import box
from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.panel import Panel
from rich.text import Text

from .. import APP_NAME, TAGLINE, __author__, __publisher__, __version__
from ..core.models import AppConfig
from ..core.providers import PROVIDERS, provider_label
from ..core.providers.base import STATUS_CONNECTED, STATUS_UNKNOWN
from .logo import render_logo_lines

# --- the dimensional grid (1-based screen columns; the panel starts at 1) ----
PANEL_WIDTH = 88  # outer width; the panel never exceeds this
_INNER_WIDTH = PANEL_WIDTH - 2  # 86: inside the rounded border

_LEFT_ANCHOR = 5  # logo starts here
_BRAND_ANCHOR = 18  # SEEDCODE CLI starts here
_DIVIDER_ANCHOR = 42  # vertical bar, one blank column on each side
_RIGHT_ANCHOR = 47  # right-hand labels start here

# Inner 0-based indices (border takes col 1, padding takes col 2).
_LEFT_IDX = _LEFT_ANCHOR - 2  # 3
_BRAND_IDX = _BRAND_ANCHOR - 2  # 16
_DIVIDER_IDX = _DIVIDER_ANCHOR - 2  # 40
_RIGHT_IDX = _RIGHT_ANCHOR - 2  # 45

# Compact icon footprint (rows) from the 8-column compact raster.
_LOGO_ROWS = 5

_ROWS = 8  # content rows inside the border (spec: exactly 8)
_INFO_ROWS = 6  # populated rows: header, tagline, provider, model, mode, status

# Right-hand label column: "Provider ", "Model    ", "Mode     ", "Status  "
_LABEL_WIDTH = 9

# Widest model display at full design width; shrinks with the terminal.
_MODEL_CLIP = 24

# Below this width (or on legacy consoles) the compact banner renders.
_MIN_WIDTH = 64

_WORDMARK = "SEEDCODE CLI"
_DIVIDER = "│"


# --- dynamic session values (real application state only) -------------------
def _provider_value(config: AppConfig) -> str:
    """Provider display label ('Not configured' before setup)."""
    provider = PROVIDERS.get(config.provider)
    ready = provider is not None and (
        not provider.requires_key or bool(config.get_api_key().strip())
    )
    return provider_label(config.provider) if ready else "Not configured"


def _model_value(config: AppConfig) -> str:
    if not config.model:
        return "no model"
    if config.model == "auto":
        return "Auto"
    return config.model


def _mode_value(config: AppConfig) -> str:
    """The user-facing mode: Chat or Assist (never Agent/Desktop)."""
    return "Assist" if config.agent_mode else "Chat"


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


def _clip(value: str, limit: int) -> str:
    """Ellipsize a *display* value; the configured value is never modified."""
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)] + "…"


# --- the reference layout ----------------------------------------------------
def _labelled(label: str, value: Text | str, value_style: str = "seed.text") -> Text:
    """One two-column 'Label    value' information row."""
    line = Text(no_wrap=True, overflow="crop")
    line.append(f"{label:<{_LABEL_WIDTH}}", style="seed.dim")
    if isinstance(value, Text):
        line.append_text(value)
    else:
        line.append(value, style=value_style)
    return line


def _branding_section() -> list[Text]:
    """Left section rows: wordmark + tagline, aligned at the brand anchor."""
    wordmark = Text(_WORDMARK, style="bold seed.primary", no_wrap=True)
    tagline = Text(TAGLINE, style="seed.dim", no_wrap=True)
    return [wordmark, tagline]


def _info_section(
    config: AppConfig, legacy: bool, value_clip: int, info_width: int
) -> list[Text]:
    """Right section rows: studio header, tagline, provider/model/mode/status.

    ``value_clip`` bounds Provider/Model values so long model ids never push
    Mode, Status, the divider or the border out of position; every row is
    clipped to ``info_width`` so nothing ever overflows the right section.
    """
    ready_mark = "o" if legacy else "●"
    idle_mark = "-" if legacy else "○"

    header = Text(no_wrap=True, overflow="crop")
    header.append(APP_NAME, style="seed.primary")
    header.append(" | ", style="seed.dim")
    header.append(__publisher__, style="seed.dim")
    header.truncate(info_width, overflow="ellipsis")
    tagline = Text(TAGLINE, style="seed.dim", no_wrap=True)
    tagline.truncate(info_width, overflow="ellipsis")

    return [
        header,
        tagline,
        _labelled("Provider", _clip(_provider_value(config), value_clip), "seed.primary"),
        _labelled("Model", _clip(_model_value(config), value_clip)),
        _labelled("Mode", _mode_value(config), "seed.accent"),
        _labelled("Status", _status_value(config, ready_mark, idle_mark)),
    ]


class _DashboardGrid:
    """Row-based renderable: every row is padded to the exact inner width.

    Cells carry their anchor column in :class:`Text` metadata, so the
    divider always lands on its anchor column and the right section always
    starts at its anchor — no auto-sizing surprises at any terminal width.
    """

    def __init__(self, inner_width: int, divider_index: int) -> None:
        self._inner = inner_width
        self._divider_index = divider_index
        self._rows: list[tuple[list[tuple[int, Text]], bool]] = []

    def add_row(self, cells: list[tuple[int, Text]], divider: bool = True) -> None:
        """Add a row of ``(inner_index, text)`` cells, in anchor order."""
        self._rows.append((cells, divider))

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        divider_style = console.get_style("seed.primary", default="")
        for cells, divider in self._rows:
            line = Text(no_wrap=True, overflow="crop")
            end = 0  # next unwritten inner column index
            drawn = False
            for start, cell in cells:
                # The divider sits between the left and right sections;
                # draw it as soon as the next cell would pass its column.
                if (
                    divider
                    and not drawn
                    and start > self._divider_index
                    and end <= self._divider_index
                ):
                    line.append(" " * (self._divider_index - end))
                    line.append(_DIVIDER, style=divider_style)
                    end = self._divider_index + 1
                    drawn = True
                if start > end:
                    line.append(" " * (start - end))
                    end = start
                line.append_text(cell)
                end += cell.cell_len
            if divider and not drawn and end <= self._divider_index:
                line.append(" " * (self._divider_index - end))
                line.append(_DIVIDER, style=divider_style)
                end = self._divider_index + 1
            if end < self._inner:
                line.append(" " * (self._inner - end))
            yield line

    def __rich_measure__(
        self, console: Console, options: ConsoleOptions
    ) -> Measurement:
        return Measurement(self._inner, self._inner)


def _reference_dashboard(
    config: AppConfig,
    legacy: bool,
    value_clip: int,
    panel_width: int,
    right_index: int,
) -> Panel:
    """The full reference layout as a rounded green panel.

    Every row is assembled on the fixed horizontal grid (anchors 5 / 18 /
    42 / 47 at design width); the panel is exactly ``panel_width`` columns
    wide and the content is exactly :data:`_ROWS` rows tall.
    """
    logo_lines = render_logo_lines(legacy, compact=True)
    branding = _branding_section()
    info = _info_section(config, legacy, value_clip, _LABEL_WIDTH + value_clip)

    grid = _DashboardGrid(panel_width - 2, _DIVIDER_IDX)
    last_divider_row = _INFO_ROWS  # populated rows 1..6 (0-based)

    for row in range(_ROWS):
        cells: list[tuple[int, Text]] = []
        if 1 <= row <= _LOGO_ROWS and row - 1 < len(logo_lines):
            cells.append((_LEFT_IDX, logo_lines[row - 1]))
        if row == 1:
            cells.append((_BRAND_IDX, branding[0]))
        elif row == 2:
            cells.append((_BRAND_IDX, branding[1]))
        if 1 <= row <= last_divider_row:
            index = row - 1
            if index < len(info):
                cells.append((right_index, info[index]))
            grid.add_row(cells, divider=True)
        else:
            grid.add_row(cells, divider=False)  # blank padding rows

    return Panel(
        grid,
        title=f"Seed code v{__version__}",
        title_align="left",
        border_style="seed.primary",
        box=box.ASCII if legacy else box.ROUNDED,
        padding=(0, 0),  # grid rows span the full inner width, border to border
        expand=False,
        width=panel_width,
    )


# --- compact fallback (narrow terminals / legacy Windows consoles) -----------
def _status_line(config: AppConfig, legacy: bool) -> Text:
    """The single information-dense content row for the compact fallback."""
    bullet = "*" if legacy else "•"
    ready_mark = "o" if legacy else "●"
    idle_mark = "-" if legacy else "○"
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append(_clip(_provider_value(config), _MODEL_CLIP), style="seed.primary")
    line.append(f" {bullet} ", style="seed.dim")
    line.append(_clip(_model_value(config), _MODEL_CLIP), style="seed.text")
    line.append(f" {bullet} ", style="seed.dim")
    line.append(_mode_value(config), style="seed.accent")
    line.append(f" {bullet} ", style="seed.dim")
    line.append_text(_status_value(config, ready_mark, idle_mark))
    return line


def _compact_dashboard(
    console: Console, config: AppConfig, legacy: bool, width: int
) -> Panel:
    """Three-line banner: top border, status row, bottom border."""
    line = _status_line(config, legacy)
    return Panel(
        line,
        title=f"{APP_NAME} v{__version__}",
        title_align="left",
        border_style="seed.primary",
        box=box.ASCII if legacy else box.ROUNDED,
        padding=(0, 1),
        expand=False,
        width=max(44, min(width, console.measure(line).maximum + 6)),
    )


def console_size(console: Console) -> int:
    """Current terminal width (80 when unknown, e.g. tests/CI)."""
    return console.size.width or 80


def render_dashboard(console: Console, config: AppConfig) -> None:
    """Render the startup dashboard to ``console`` (once, at launch)."""
    legacy = console.legacy_windows
    width = console_size(console)
    if legacy or width < _MIN_WIDTH:
        console.print(_compact_dashboard(console, config, legacy, width))
        return

    # Responsive ladder: cap at the 88-column design width (never stretch
    # on wide terminals), then shrink the value clip so long model ids
    # never push Mode/Status, the divider or the border out of position.
    # Only if the panel is narrower than the design grid does the
    # right-hand anchor move left toward the divider (never past its
    # blank column), and the clip shrink further if even that cannot fit.
    panel_width = min(width, PANEL_WIDTH)
    inner = panel_width - 2
    available = inner - _RIGHT_IDX  # design: 41 columns for the right section
    # Reserve one trailing blank column so values never touch the border.
    value_clip = max(10, min(_MODEL_CLIP, available - _LABEL_WIDTH - 1))
    if available >= _LABEL_WIDTH + value_clip + 1:
        right_index = _RIGHT_IDX  # the design anchor fits — keep it exact
    else:
        right_index = _DIVIDER_IDX + 2  # divider + its blank column
        value_clip = max(8, inner - right_index - _LABEL_WIDTH - 1)
    console.print(_reference_dashboard(config, legacy, value_clip, panel_width, right_index))
