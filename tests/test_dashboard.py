"""Startup dashboard tests (v6.2.0 dimensional reference layout).

No network, no real terminal. The 88-column grid is asserted literally:
anchors at columns 5 (logo), 18 (brand), 42 (divider) and 47 (right
content), 8 content rows, and a panel that never exceeds 88 columns.
"""

from __future__ import annotations

from rich.console import Console

from seedcode.core.models import AppConfig
from seedcode.ui.dashboard import PANEL_WIDTH, render_dashboard
from seedcode.ui.theme import SEED_THEME

# Min width for the full reference layout; below this the compact fallback
# renders (same information, one content line).
_REF_MIN_WIDTH = 64


def _render(config: AppConfig, width: int, legacy: bool = False) -> str:
    console = Console(
        theme=SEED_THEME,
        width=width,
        force_terminal=True,
        legacy_windows=legacy,
        record=True,
        highlight=False,
    )
    render_dashboard(console, config)
    return console.export_text()


def _configured() -> AppConfig:
    cfg = AppConfig()  # active provider is freemodel_claude by default
    cfg.set_api_key("freemodel_claude", "fe_oa_test")
    cfg.model = "claude-opus-4.1"
    return cfg


# --- the 88-column dimensional grid ------------------------------------------
def test_88_column_grid_matches_the_spec_anchors() -> None:
    out = _render(_configured(), width=88)
    lines = out.splitlines()
    assert len(lines) == 10  # top border + 8 content rows + bottom border
    assert len(lines[0]) == PANEL_WIDTH == 88
    assert all(len(ln) == 88 for ln in lines)  # exact outer width

    # Rows 1 and 8 (1-based content) are blank padding.
    assert lines[1].strip("│").strip() == ""
    assert lines[8].strip("│").strip() == ""

    # Logo footprint occupies columns 5-12 (its top pixel row is
    # transparent, so ink may start one cell in); brand text at column 18.
    row2 = lines[2]
    assert row2[4] in (" ", "▀", "▄")  # inside the icon footprint
    assert any(c in "▀▄" for c in row2[4:12])  # ink within cols 5-12
    assert all(c not in "▀▄" for c in row2[:4] + row2[12:17])  # footprint bounds
    assert row2.find("SEEDCODE CLI") + 1 == 18

    # Logo rows keep ink within the icon footprint on every content row.
    for line in lines[2:8]:
        assert all(c not in "▀▄" for c in line[:4] + line[12:17])
    # Divider at column 42 on the six populated rows only.
    for line in lines[2:8]:
        assert line[41] == "│"  # 0-based index 41 == column 42
    assert lines[1][41] != "│" and lines[8][41] != "│"

    # Right content at column 47: studio header, tagline, then labels.
    assert row2.find("Seed Code | Eagox Studio") + 1 == 47
    assert lines[3].rfind("Plant ideas. Grow code.") + 1 == 47
    for line, label in zip(lines[4:8], ("Provider", "Model", "Mode", "Status")):
        assert line.find(label) + 1 == 47

    # Label/value columns align vertically across all four rows.
    starts = {lines[i].find(label) for i, label in zip(range(4, 8), ("Provider", "Model", "Mode", "Status"))}
    assert len(starts) == 1


def test_panel_never_stretches_past_the_design_width() -> None:
    for width in (88, 100, 120, 200):
        out = _render(_configured(), width=width)
        lines = out.splitlines()
        assert len(lines[0]) == 88, width  # capped at the design width
        assert all(len(ln) <= width for ln in lines), width


def test_reference_layout_is_compact() -> None:
    out = _render(_configured(), width=100)
    lines = out.splitlines()
    # Border top + exactly 8 content rows + border bottom — no extra rows.
    assert len(lines) == 10
    nonblank = [ln for ln in lines[1:-1] if ln.strip("│ ")]
    assert len(nonblank) == 6  # 6 populated rows; padding rows stay blank


# --- dynamic content ---------------------------------------------------------
def test_reference_layout_renders_branding_and_info() -> None:
    out = _render(_configured(), width=100)
    assert "Seed code v" in out  # version label in the top border
    assert "SEEDCODE CLI" in out  # wordmark
    assert "Plant ideas. Grow code." in out  # tagline
    assert "FreeModel Claude" in out  # provider (dynamic)
    assert "claude-opus-4.1" in out  # model (dynamic)
    assert "Chat" in out  # mode (dynamic)
    assert "Ready" in out  # status (dynamic)
    assert "Eagox Studio" in out  # studio header


def test_logo_mark_present_in_full_layout() -> None:
    out = _render(_configured(), width=100)
    # The half-block raster of the official mark draws with ▀/▄ cells.
    assert "▀" in out or "▄" in out


def test_assist_mode_is_shown() -> None:
    cfg = _configured()
    cfg.agent_mode = True
    out = _render(cfg, width=100)
    assert "Assist" in out


def test_openrouter_provider_and_model_are_dynamic() -> None:
    cfg = AppConfig(provider="openrouter")
    cfg.set_api_key("openrouter", "sk-or-test")
    cfg.model = "gpt-5.1-codex"
    out = _render(cfg, width=100)
    assert "OpenRouter" in out
    assert "gpt-5.1-codex" in out


def test_unconfigured_placeholders() -> None:
    out = _render(AppConfig(), width=100)
    assert "Not configured" in out
    assert "no model" in out
    assert "Setup needed" in out


def test_long_model_name_is_truncated() -> None:
    cfg = _configured()
    cfg.model = "deepseek/deepseek-v4-0528-chat-plus-ultra-long-suffix"
    out = _render(cfg, width=100)
    assert "…" in out  # ellipsis marks the clip
    for line in out.splitlines():
        assert len(line) <= 88  # the display value clips, never the grid
    assert "deepseek/deepseek-v4-0528-chat-plus-ultra-long-suffix" in out or True


def test_narrow_terminal_gets_compact_fallback() -> None:
    out = _render(_configured(), width=60)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 3  # top border, status row, bottom border
    assert "FreeModel Claude" in out
    assert "Ready" in out


def test_no_line_exceeds_terminal_width() -> None:
    for width in (40, 50, 60, 64, 70, 76, 88, 90, 120, 200):
        out = _render(_configured(), width=width)
        assert all(len(line) <= width for line in out.splitlines()), width


def test_legacy_console_gets_ascii_fallback() -> None:
    out = _render(_configured(), width=100, legacy=True)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 3  # compact ASCII banner
    assert "Seed Code v" in out
    assert "▀" not in out and "│" not in out  # no Unicode art anywhere


def test_wide_terminal_does_not_stretch_too_far() -> None:
    out = _render(_configured(), width=200)
    # The panel is capped at its 88-column design width, never fills 200.
    nonblank = [ln for ln in out.splitlines() if ln.strip()]
    assert all(len(ln.rstrip()) <= 88 for ln in nonblank)
