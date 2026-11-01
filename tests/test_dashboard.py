"""Startup dashboard tests (v9.1.0 logo branding).

The startup screen is now the Seed Code ANSI wordmark logo followed by a
compact, information-rich block with the live session state::

    ╭─ Seed Code CLI v9.1.1 ──────────────────────────────────────────────╮
    │   ▄█████ ▄▄▄▄▄ ▄▄▄▄▄ ▄▄▄▄    ▄█████  ▄▄▄  ▄▄▄▄  ▄▄▄▄▄   ▄█████ ██ ...│
    │   ...                                                                │
    │                                                                      │
    │   Seed Code CLI v9.1.1                                               │
    │   Plant ideas. Grow code.                                            │
    │   Provider   OpenRouter                                              │
    │   Model      gpt-5.1-codex                                           │
    │   Mode       Chat                                                    │
    │   Status     ● Ready                                                 │
    ╰──────────────────────────────────────────────────────────────────────╯

These tests require the exact logo at Unicode widths, a text-wordmark fallback
on consoles that cannot draw the block glyphs, real runtime state (never a
faked "Ready"), API-key rows only where a key is actually needed, and
responsive behaviour with an ASCII fallback.
"""

from __future__ import annotations

import importlib.util
import io
import re

from rich.console import Console

from seedcode import APP_NAME, __version__
from seedcode.core.models import AppConfig
from seedcode.ui import UI
from seedcode.ui.dashboard import LOGO_LINES, PANEL_WIDTH, render_dashboard
from seedcode.ui.theme import SEED_THEME

# Block glyphs that make up the fixed logo (branding, not generated art).
_LOGO_GLYPHS = set("█▄▀")
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


# --- the fixed logo is the primary startup branding --------------------------
def test_the_exact_logo_is_the_primary_branding() -> None:
    for cfg in (_byok(), AppConfig()):
        out = _render(cfg, 100)
        for line in LOGO_LINES:
            assert line in out, line


def test_old_branding_is_replaced_at_unicode_widths() -> None:
    out = _render(_byok(), 100)
    # The old "SeedCode Cli / ai coding assistant" startup branding is gone.
    assert "SeedCode Cli" not in out
    assert "ai coding assistant" not in out
    assert "AI CODING AGENT" not in out  # that was the fallback wordmark


def test_legacy_consoles_get_the_text_wordmark_not_block_art() -> None:
    out = _render(_byok(), 100, legacy=True)
    # A console that cannot draw the logo still gets a structured panel...
    assert "Seed Code" in out
    assert "AI CODING AGENT" in out
    # ...but never the unencodable block art.
    for line in out.splitlines():
        assert not (_LOGO_GLYPHS & set(line)), line


def test_logo_is_not_a_separate_module() -> None:
    """The removed logo/banner modules must never come back as loose modules."""
    assert importlib.util.find_spec("seedcode.ui.logo") is None
    assert importlib.util.find_spec("seedcode.ui.banner") is None


# --- structured, not bare -----------------------------------------------------
def test_dashboard_is_structured_and_compact() -> None:
    lines = _lines(_byok())
    assert 8 <= len(lines) <= 14, lines  # logo + info, not a splash screen
    assert lines[0].startswith(f"╭─ {APP_NAME} CLI v{__version__}")
    assert lines[-1].startswith("╰")


def test_identity_and_tagline_are_shown() -> None:
    out = _render(_byok())
    assert f"{APP_NAME} CLI v{__version__}" in out
    assert "Plant ideas. Grow code." in out


def test_runtime_state_is_labelled_once_per_row() -> None:
    out = _render(_byok())
    for label in ("Provider", "Model", "Mode", "Status"):
        assert len(re.findall(rf"\b{label}\b", out)) == 1, label


def test_byok_provider_values_are_dynamic() -> None:
    out = _render(_byok())
    assert "OpenRouter" in out
    assert "gpt-5.1-codex" in out
    # v9.2.0: one unified coding agent — the Mode row names the capability,
    # there is no Chat mode to name any more.
    assert "Coding Agent" in out
    assert "Ready" in out


def test_mode_and_status_are_separate_rows() -> None:
    lines = _lines(_byok(), 88)
    mode = next(ln for ln in lines if re.search(r"\bMode\b", ln))
    status = next(ln for ln in lines if re.search(r"\bStatus\b", ln))
    assert "Coding Agent" in mode
    assert "Ready" in status


# --- real runtime content -----------------------------------------------------
def test_wide_terminals_show_the_whole_default_model() -> None:
    cfg = AppConfig(provider="default", model="cohere/north-mini-code:free")
    assert "cohere/north-mini-code:free" in _render(cfg, width=88)


def test_panel_never_stretches_past_the_design_width() -> None:
    for width in (120, 160, 200):
        for line in _lines(_byok(), width):
            assert len(line) <= PANEL_WIDTH, (width, line)


def test_wide_terminals_do_not_stretch_the_dashboard() -> None:
    assert _lines(_byok(), 200) == _lines(_byok(), 120)


def test_long_model_name_is_clipped_to_display_width() -> None:
    cfg = _byok()
    cfg.model = "deepseek/" + "x" * 120
    out = _render(cfg, width=100)
    assert "…" in out
    for line in out.splitlines():
        assert len(line) <= 100


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


def test_single_agent_mode_and_removed_modes_are_not_named(monkeypatch, tmp_path) -> None:
    """v9.2.0: one coding agent, and the removed mode commands stay removed."""
    from seedcode import codemode_state as cms
    from seedcode.commands import CommandContext, dispatch

    ui = _StubUI()
    cfg = _byok()
    assert "Coding Agent" in _render(cfg)
    assert "Agent Mode" not in _render(cfg)
    assert "Code Mode" not in _render(cfg)

    cms.reset()
    monkeypatch.chdir(tmp_path)
    try:
        # The former mode-switch commands (/chat /agent /code /mode /codemode)
        # were removed with the mode system: they must be reported as unknown
        # instead of silently pretending to switch to a mode that is gone.
        for command in ("/agent on", "/mode chat", "/codemode on"):
            dispatch(CommandContext(ui=ui, config=cfg, engine=None), command)
        assert any("Unknown command" in m for m in ui.messages)
        assert "Coding Agent" in _render(cfg)  # the session never left the agent
    finally:
        cms.reset()


# --- the API-key row follows the provider's REAL requirement -----------------
def test_api_key_row_hidden_for_providers_that_need_no_key() -> None:
    assert "API Key" not in _render(AppConfig(provider="default"))
    assert "API Key" not in _render(AppConfig(provider="ollama", model="llama3.2"))


def test_api_key_row_shown_masked_for_key_providers() -> None:
    out = _render(_byok())
    assert "API Key" in out
    assert "sk-or-test" not in out
    assert "*" in out


def test_api_key_row_warns_when_missing() -> None:
    cfg = AppConfig(provider="openrouter", model="gpt-5.1-codex")
    assert "Not set" in _render(cfg)


# --- unconfigured never fakes readiness --------------------------------------
def test_unconfigured_placeholders() -> None:
    out = _render(AppConfig())
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
        assert "Coding Agent" in out


def test_very_narrow_terminals_render_plain_lines() -> None:
    for width in (28, 32, 39):
        lines = _lines(_byok(), width)
        assert len(lines) == 2, (width, lines)
        out = "\n".join(lines)
        assert "Seed Code" in out
        assert not (_BOX_GLYPHS & set(lines[0])), lines[0]


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
    for expected in ("OpenRouter", "gpt-5.1-codex", "Coding Agent", "Ready"):
        assert expected in out, out


def test_removed_mode_command_is_reported_and_state_is_untouched() -> None:
    """The former /mode chat switch is gone: reported, not silently faked."""
    from seedcode.commands import CommandContext, dispatch

    ui = _ui()
    config = AppConfig(provider="default", model="cohere/north-mini-code:free")
    dispatch(CommandContext(ui=ui, config=config, engine=None), "/mode chat")
    out = ui.console.export_text()
    assert "Unknown command" in out
    # The provider/model state the user actually has is untouched.
    assert config.provider == "default"
    assert config.model == "cohere/north-mini-code:free"


def test_legacy_console_gets_an_ascii_panel() -> None:
    out = _render(_byok(), legacy=True)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[0].startswith("+- Seed Code CLI")
    assert lines[-1].startswith("+-")
    assert "o Ready" in out or "o Connected" in out
    for glyph in "●○╭╮╰╯│─█▄▀":
        assert glyph not in out, glyph


def test_panel_is_wide_and_short() -> None:
    assert PANEL_WIDTH == 96
