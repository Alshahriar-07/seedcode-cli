"""Startup header tests (v6.2.5 minimal layout).

No network, no real terminal. The launch screen is a small borderless block:

    Seed Code CLI v6.2.5
    Provider  Default
    Model     <model>
    Mode      Chat
    Status    ● Ready

These tests lock in the v6.2.5 UI direction: **no ASCII logo**, no panel/box,
no decorative art, real runtime state shown exactly once, and never a faked
"Ready" for an unconfigured session.
"""

from __future__ import annotations

import re

from rich.console import Console

from seedcode import APP_NAME, __version__
from seedcode.core.models import AppConfig
from seedcode.ui.dashboard import render_dashboard
from seedcode.ui.theme import SEED_THEME

# Glyphs that would mean ASCII art came back: full/partial block shading and
# every box-drawing corner or edge a banner would need.
_ART_GLYPHS = set("█▓▒░▀▄▌▐■□▪▫") | set("╭╮╰╯┌┐└┘─│┃━┃")


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


# --- no logo, no box, no art -------------------------------------------------
def test_no_ascii_logo_or_box_art_anywhere() -> None:
    for cfg in (_byok(), AppConfig()):
        for width in (80, 100, 200):
            for line in _lines(cfg, width):
                assert not (_ART_GLYPHS & set(line)), line


def test_logo_module_is_gone() -> None:
    """v6.2.5 removed the ASCII logo outright — no logo module may return."""
    import importlib.util

    assert importlib.util.find_spec("seedcode.ui.logo") is None
    assert importlib.util.find_spec("seedcode.ui.banner") is None


def test_header_is_small() -> None:
    # Title + four or five value rows; the caller adds one hint line on top.
    assert len(_lines(_byok())) <= 6
    assert len(_lines(AppConfig())) <= 6


def test_title_is_the_brand_line() -> None:
    lines = _lines(_byok())
    assert lines[0] == f"{APP_NAME} CLI v{__version__}"
    assert lines[0] == "Seed Code CLI v6.2.5"
    assert "Seed code" not in "\n".join(lines)  # brand casing everywhere


# --- dynamic content, shown once ---------------------------------------------
def test_runtime_state_is_labelled_once_per_row() -> None:
    out = _render(_byok())
    # Whole words, so "Mode" is not matched inside "Model".
    for label in ("Provider", "Model", "Mode", "Status"):
        assert len(re.findall(rf"\b{label}\b", out)) == 1, label


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
    cfg = AppConfig(provider="default", model="nvidia/nemotron-3-super-120b-a12b:free")
    out = _render(cfg)
    assert "Default" in out
    assert "Ready" in out


# --- fits the terminal -------------------------------------------------------
def test_long_model_name_is_clipped_to_display_width() -> None:
    cfg = _byok()
    cfg.model = "deepseek/" + "x" * 120
    out = _render(cfg, width=100)
    assert "…" in out  # ellipsis marks the clip
    for line in out.splitlines():
        assert len(line) <= 100


def test_no_line_exceeds_terminal_width() -> None:
    for width in (40, 50, 60, 80, 88, 100, 200):
        for cfg in (_byok(), AppConfig()):
            out = _render(cfg, width=width)
            assert all(len(line) <= width for line in out.splitlines()), (width, cfg.provider)


def test_legacy_console_gets_ascii_marks_only() -> None:
    out = _render(_byok(), legacy=True)
    assert "o Ready" in out or "o Connected" in out
    assert "●" not in out


def test_wide_terminals_do_not_stretch_the_header() -> None:
    narrow = _lines(_byok(), width=100)
    assert _lines(_byok(), width=200) == narrow
