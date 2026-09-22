"""Startup dashboard tests (v6.2.5 restored richer layout).

The startup screen is the previous structured Seed Code dashboard — a
bordered reference panel with a branding cell, a divider and a live info
section::

    ╭─ Seed Code CLI v7.1.0 ───────────────────────────────────────────────────────────────────────╮
    │                                                                                              │
    │   Seed Code                                │ Seed Code  |  Eagox Studio                      │
    │   AI CODING AGENT                          │ Plant ideas. Grow code.                         │
    │                                            │ Provider   Default                              │
    │                                            │ Model      cohere/north-mini-code:free          │
    │                                            │ Mode       Chat  •  ● Ready                     │
    ╰──────────────────────────────────────────────────────────────────────────────────────────────╯

…with exactly one permanent change: **the ASCII logo is gone**. These tests
lock that in — no pixel/block art can come back, no logo module may return —
while still requiring a structured, information-rich screen (not a bare
five-line header), real runtime state, responsive behaviour and an ASCII
fallback for consoles that cannot draw the glyphs.
"""

from __future__ import annotations

import importlib.util
import io
import re

from rich.console import Console

from seedcode import APP_NAME, __version__
from seedcode.core.models import AppConfig
from seedcode.ui import UI
from seedcode.ui.dashboard import PANEL_WIDTH, render_dashboard
from seedcode.ui.theme import SEED_THEME

# Pixel/block glyphs ARE ascii-art branding: never allowed, at any width.
_ART_GLYPHS = set("█▓▒░▀▄▌▐■□▪▫")
# ASCII-art fallback cues (a '#'-raster logo, drawn large).
_ART_RUNS = ("██", "▓▓", "░░", "▄▄", "###")
# Glyphs the structured layout is allowed to use.
_BOX_GLYPHS = set("╭╮╰╯│─+-|")


def _render(config: AppConfig, width: int = 100, legacy: bool = False) -> str:
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


def _lines(config: AppConfig, width: int = 100, legacy: bool = False) -> list[str]:
    return [ln for ln in _render(config, width, legacy).splitlines() if ln.strip()]


def _byok() -> AppConfig:
    """A configured provider that DOES need a key."""
    cfg = AppConfig(provider="openrouter")
    cfg.set_api_key("openrouter", "sk-or-test")
    cfg.model = "gpt-5.1-codex"
    return cfg


# --- the logo stays gone ------------------------------------------------------
def test_no_ascii_logo_or_pixel_art_anywhere() -> None:
    for cfg in (_byok(), AppConfig()):
        for width in (40, 50, 64, 80, 100, 200):
            for legacy in (False, True):
                lines = _lines(cfg, width, legacy)
                joined = "\n".join(lines)
                assert not (_ART_GLYPHS & set(joined)), joined
                for run in _ART_RUNS:
                    assert run not in joined, (run, joined)


def test_logo_module_is_gone() -> None:
    """The removed logo/banner modules must never come back."""
    assert importlib.util.find_spec("seedcode.ui.logo") is None
    assert importlib.util.find_spec("seedcode.ui.banner") is None


def test_brand_is_plain_text() -> None:
    out = _render(_byok())
    assert "Seed Code" in out
    assert "AI CODING AGENT" in out
    assert "Plant ideas. Grow code." in out
    assert "Eagox Studio" in out


# --- structured, not bare -----------------------------------------------------
def test_dashboard_is_structured_and_compact() -> None:
    lines = _lines(_byok())
    assert 6 <= len(lines) <= 12, lines  # richer than 5 lines, no splash screen
    assert lines[0].startswith(f"╭─ Seed Code CLI v{__version__}")
    assert lines[-1].startswith("╰")
    assert any("│" in line for line in lines)  # the section divider is drawn


def test_left_cell_is_brand_only_and_right_cell_carries_identity() -> None:
    """The reference split: branding left of the divider, identity right."""
    lines = _lines(_byok(), 88)
    cells = [ln.split("│") for ln in lines if "│" in ln]
    left = [cell[1].strip() for cell in cells if cell[1].strip()]
    right = [cell[2].strip() for cell in cells]
    right = [cell for cell in right if cell]
    assert left[:2] == ["Seed Code", "AI CODING AGENT"]
    assert right[0] == "Seed Code  |  Eagox Studio"
    assert right[1] == "Plant ideas. Grow code."


def test_mode_row_carries_the_live_status() -> None:
    """Mode and status share one row, in the reference's order."""
    mode = next(ln for ln in _lines(_byok(), 88) if re.search(r"\bMode\b", ln))
    assert "Chat" in mode and "Ready" in mode
    assert mode.index("Chat") < mode.index("Ready")


def test_eighty_columns_show_the_whole_default_model() -> None:
    """The info section slides left so a common 80-column terminal still fits
    the entire default model name — no clipping of the value that matters."""
    cfg = AppConfig(provider="default", model="cohere/north-mini-code:free")
    assert "cohere/north-mini-code:free" in _render(cfg, width=80)
    assert "Model      cohere/north-mini-code:free" in _render(cfg, width=88)


def test_title_is_the_brand_line() -> None:
    lines = _lines(_byok())
    title = f"{APP_NAME} CLI v{__version__}"
    assert title in lines[0]
    assert "Seed code" not in "\n".join(lines)  # brand casing everywhere


def test_panel_never_stretches_past_the_design_width() -> None:
    for width in (120, 160, 200):
        for line in _lines(_byok(), width):
            assert len(line) <= PANEL_WIDTH, (width, line)


def test_wide_terminals_do_not_stretch_the_dashboard() -> None:
    assert _lines(_byok(), 200) == _lines(_byok(), 120)


# --- dynamic content, shown once ---------------------------------------------
def test_runtime_state_is_labelled_once_per_row() -> None:
    out = _render(_byok())
    # Whole words, so "Mode" is not matched inside "Model".
    for label in ("Provider", "Model", "Mode"):
        assert len(re.findall(rf"\b{label}\b", out)) == 1, label
    # Mode and status share one row — there is no second, standalone row.
    assert "Status" not in out


def test_byok_provider_values_are_dynamic() -> None:
    out = _render(_byok())
    assert "OpenRouter" in out
    assert "gpt-5.1-codex" in out
    assert "Chat" in out
    assert "Ready" in out


class _StubUI:
    """Minimal UI stand-in: records messages, ignores rendering."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def _record(self, message) -> None:
        self.messages.append(str(message))

    info = dim = success = warning = error = _record

    def panel(self, body, title: str | None = None) -> None:
        self.messages.append(title or "")

    def blank(self) -> None:
        self.messages.append("")

    def confirm_desktop(self, category_label: str, description: str) -> str:
        return "n"


def test_code_mode_and_assist_mode_are_named(monkeypatch, tmp_path) -> None:
    cfg = _byok()
    cfg.agent_mode = True
    assert "Assist Mode" in _render(cfg)

    from seedcode import codemode_state as cms
    from seedcode.commands import CommandContext, dispatch
    from seedcode.commands import assist as assist_cmd
    from seedcode.commands import codemode as codemode_cmd

    cms.reset()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(codemode_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "is_available", lambda: (False, "test"))
    try:
        dispatch(CommandContext(ui=_StubUI(), config=cfg, engine=None), "/codemode on")
        assert "Code Mode" in _render(cfg)
    finally:
        cms.reset()


# --- the API-key row follows the provider's REAL requirement -----------------
def test_api_key_row_hidden_for_providers_that_need_no_key() -> None:
    assert "API Key" not in _render(AppConfig(provider="default"))
    assert "API Key" not in _render(AppConfig(provider="ollama", model="llama3.2"))


def test_api_key_row_shown_masked_for_key_providers() -> None:
    out = _render(_byok())
    assert "API Key" in out
    assert "sk-or-test" not in out  # never the raw key
    assert "*" in out  # masked form


def test_api_key_row_warns_when_missing() -> None:
    cfg = AppConfig(provider="openrouter", model="gpt-5.1-codex")
    assert "Not set" in _render(cfg)


# --- unconfigured never fakes readiness -------------------------------------
def test_unconfigured_placeholders() -> None:
    out = _render(AppConfig())
    # The built-in provider is named honestly, but with no model chosen there
    # is nothing to chat with — and "Ready" is never faked.
    assert "Default" in out
    assert "no model" in out
    assert "Setup needed" in out
    assert "Ready" not in out


def test_unconfigured_key_provider_shows_not_configured() -> None:
    out = _render(AppConfig(provider="openrouter"))
    assert "Not configured" in out
    assert "Setup needed" in out


def test_default_provider_shown_when_builtin_available(monkeypatch) -> None:
    from seedcode import default_api

    monkeypatch.setattr(
        default_api, "_load_embedded", lambda: ("builtin-key", True, "release")
    )
    cfg = AppConfig(provider="default", model="cohere/north-mini-code:free")
    out = _render(cfg)
    assert "Default" in out
    assert "cohere/north-mini-code:free" in out
    assert "Ready" in out


# --- responsive ---------------------------------------------------------------
def test_narrow_terminals_get_the_compact_panel() -> None:
    for width in (40, 48, 56, 63):
        lines = _lines(_byok(), width)
        assert len(lines) == 3, (width, lines)
        out = "\n".join(lines)
        assert "OpenRouter" in out or "OpenR" in out
        assert "Chat" in out
        assert "Ready" in out or "Rea" in out


def test_very_narrow_terminals_render_plain_lines() -> None:
    for width in (28, 32, 39):
        lines = _lines(_byok(), width)
        assert len(lines) == 2, (width, lines)
        out = "\n".join(lines)
        assert "Seed Code" in out
        assert not (_BOX_GLYPHS & set(lines[0])), lines[0]


def test_long_model_name_is_clipped_to_display_width() -> None:
    cfg = _byok()
    cfg.model = "deepseek/" + "x" * 120
    out = _render(cfg, width=100)
    assert "…" in out  # ellipsis marks the clip
    for line in out.splitlines():
        assert len(line) <= 100


def test_no_line_exceeds_terminal_width() -> None:
    for width in (24, 30, 40, 50, 60, 80, 88, 100, 200):
        for cfg in (_byok(), AppConfig()):
            out = _render(cfg, width=width)
            assert all(len(line) <= width for line in out.splitlines()), (
                width,
                cfg.provider,
            )


# --- the one-line session bar (after a mode switch) --------------------------
def _ui(width: int = 100) -> UI:
    ui = UI(plain=True)
    ui.console = Console(
        theme=SEED_THEME,
        width=width,
        file=io.StringIO(),
        force_terminal=True,
        legacy_windows=False,
        record=True,
    )
    return ui


def test_session_status_bar_is_one_line_of_live_state() -> None:
    ui = _ui()
    ui.statusbar(_byok())
    lines = [ln for ln in ui.console.export_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    out = lines[0]
    for expected in ("OpenRouter", "gpt-5.1-codex", "Chat", "Ready"):
        assert expected in out, out


def test_mode_switch_reprints_the_session_bar() -> None:
    from seedcode.commands import CommandContext, dispatch

    ui = _ui()
    config = AppConfig(provider="default", model="cohere/north-mini-code:free")
    dispatch(CommandContext(ui=ui, config=config, engine=None), "/mode chat")
    out = ui.console.export_text()
    assert "cohere/north-mini-code:free" in out
    assert "Setup needed" in out  # honest status, not a faked "Ready"


def test_legacy_console_gets_an_ascii_panel() -> None:
    out = _render(_byok(), legacy=True)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[0].startswith("+- Seed Code CLI")
    assert lines[-1].startswith("+-")
    assert "o Ready" in out or "o Connected" in out
    # No Unicode marks or borders on a console that cannot draw them.
    for glyph in "●○╭╮╰╯│─":
        assert glyph not in out, glyph


# --- final proportions (wide + short) ----------------------------------------
def test_panel_is_wide_and_short() -> None:
    """Locks the final sizing: a 96-column panel with no padding rows.

    Wider and shorter than the earlier reference: 96 columns at full width so
    the whole model name fits, and one less blank row — a single blank row
    under the title, the live rows, then the border.
    """
    assert PANEL_WIDTH == 96

    default_lines = _lines(AppConfig(provider="default", model="m"), 200)
    byok_lines = _lines(_byok(), 200)
    assert len(default_lines) == 8  # top border + blank + 5 rows + bottom border
    assert len(byok_lines) == 9  # ... plus the API Key row
    assert all(len(line) == PANEL_WIDTH for line in default_lines)

    # No blank padding row between the tagline and the first live value.
    tagline = next(i for i, ln in enumerate(byok_lines) if "Plant ideas" in ln)
    provider = next(i for i, ln in enumerate(byok_lines) if "Provider" in ln)
    assert provider == tagline + 1
