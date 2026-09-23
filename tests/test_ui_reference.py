"""Compact startup hints and single-source runtime state (v6.2.5).

Design rules under test:

* runtime state (provider / model / mode / status) is shown **once**, in the
  dashboard panel — never repeated in a footer;
* command hints are a single compact line, filtered to commands the router
  actually has;
* nothing overflows a standard terminal.
"""

from __future__ import annotations

import io
import re

from rich.console import Console

from seedcode.core.models import AppConfig
from seedcode.ui import UI
from seedcode.ui.reference import CONTROLS_HINT, INPUT_HINT, render_command_hint
from seedcode.ui.theme import SEED_THEME


def _console(width: int = 120) -> Console:
    return Console(
        theme=SEED_THEME,
        width=width,
        file=io.StringIO(),
        force_terminal=True,
        legacy_windows=False,
        record=True,
    )


def _configured() -> AppConfig:
    cfg = AppConfig(provider="openrouter", model="cohere/north-mini-code:free")
    cfg.set_api_key("openrouter", "sk-or-test")
    return cfg


def _banner(config: AppConfig, width: int = 120) -> str:
    console = _console(width)
    ui = UI(plain=True)
    ui.console = console
    ui.banner(config)
    return console.export_text()


# --- command hint ------------------------------------------------------------

def test_command_hint_is_a_single_compact_line() -> None:
    console = _console()
    render_command_hint(console)
    lines = [ln for ln in console.export_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    for name in ("help", "status", "codemode", "assist", "provider", "model"):
        assert f"/{name}" in lines[0]


def test_command_hint_only_lists_real_commands() -> None:
    from seedcode.commands import _REGISTRY

    console = _console()
    render_command_hint(console)
    text = console.export_text()
    for name in ("help", "status", "codemode", "assist", "provider", "model"):
        assert name in _REGISTRY
        assert f"/{name}" in text


# --- single source of state --------------------------------------------------

def test_runtime_state_is_shown_exactly_once() -> None:
    out = _banner(_configured())
    # Each value appears once — no footer repeating the panel.
    assert out.count("OpenRouter") == 1
    assert out.count("Chat") == 1
    # Match each label as a whole word, so "Mode" is not found inside "Model".
    # v7.2.5 gives status its own row (the spec's info block), so it appears
    # exactly once too — never repeated in a footer.
    for label in ("Provider", "Model", "Mode", "Status"):
        assert len(re.findall(rf"\b{label}\b", out)) == 1, label


def test_banner_title_uses_brand_casing() -> None:
    """The header line must read "Seed Code CLI v<version>" (not "Seed code")."""
    from seedcode import APP_NAME, __version__

    out = _banner(_configured())
    assert f"{APP_NAME} CLI v{__version__}" in out
    assert "Seed code" not in out


def test_unconfigured_banner_never_fakes_ready() -> None:
    out = _banner(AppConfig())
    assert "Ready" not in out
    assert "Setup needed" in out


# --- compactness -------------------------------------------------------------

def test_controls_hints_are_single_lines() -> None:
    assert "\n" not in CONTROLS_HINT and "\n" not in INPUT_HINT
    assert "Navigate" in CONTROLS_HINT
    assert "/help" in INPUT_HINT


def test_banner_is_compact_across_widths() -> None:
    cfg = _configured()
    for width in (80, 100, 120, 160):
        out = _banner(cfg, width).splitlines()
        assert len(out) <= 24, width
        assert all(len(line) <= width for line in out), width
