"""v6.2.5 core tests: version, authoritative defaults, .env loading, /status.

No network. These lock in the reliability guarantees added in the v6.2.5
rebuild: one version source, one defaults source, a project ``.env`` that is
read without ever being logged, and a command router that never silently
ignores unknown input.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedcode import __version__
from seedcode import defaults
from seedcode.commands import CommandContext, dispatch, is_command
from seedcode.config import manager
from seedcode.core.models import AppConfig


class _StubUI:
    """Records every message the UI would print."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.panels: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(str(message))

    def dim(self, message: str) -> None:
        self.messages.append(str(message))

    def success(self, message: str) -> None:
        self.messages.append(str(message))

    def warning(self, message: str) -> None:
        self.messages.append(str(message))

    def error(self, message: str) -> None:
        self.messages.append(str(message))

    def panel(self, body, title: str | None = None) -> None:
        self.panels.append(title or "")

    def blank(self) -> None:
        self.messages.append("")

    def confirm_desktop(self, category_label: str, description: str) -> str:
        return "n"


def _ctx(config: AppConfig | None = None) -> tuple[_StubUI, CommandContext]:
    ui = _StubUI()
    return ui, CommandContext(ui=ui, config=config or AppConfig(), engine=None)


# --- version -----------------------------------------------------------------

def test_version_is_current_release() -> None:
    # v7.1.0: the canonical version lives only in seedcode/__init__.py.
    assert __version__ == "7.1.0"


# --- authoritative defaults --------------------------------------------------

def test_defaults_are_a_single_source() -> None:
    # The shipped provider is the built-in Default connection (no user key),
    # which routes to OpenRouter's API as its backend, and a fresh install
    # ships already pointed at the required free coding model.
    assert defaults.DEFAULT_PROVIDER == "default"
    assert defaults.DEFAULT_BACKEND == "openrouter"
    assert defaults.DEFAULT_MODEL == "cohere/north-mini-code:free"
    assert defaults.DEFAULT_API_ENV == "OPENROUTER_API_KEY"


def test_default_provider_is_registered() -> None:
    from seedcode.core.providers import PROVIDERS

    assert defaults.DEFAULT_PROVIDER in PROVIDERS


def test_fresh_config_ships_the_default_model_and_needs_no_key() -> None:
    """First run: Default provider + the required model, still no user key."""
    from seedcode.core.providers import provider_requires_key

    config = AppConfig()
    manager.apply_default_selection(config)

    assert config.provider == "default"
    assert config.model == "cohere/north-mini-code:free"
    assert provider_requires_key("default") is False
    assert config.get_api_key("openrouter") == ""  # no key borrowed anywhere


def test_default_model_is_only_the_default_providers_model() -> None:
    """The new default must not leak into any other provider's model slot."""
    config = AppConfig()
    manager.apply_default_selection(config)
    config.set_api_key("openrouter", "sk-or-test")

    config.provider = "openrouter"
    assert config.model == ""  # OpenRouter has no model of its own yet
    config.model = "deepseek/deepseek-v4-flash-0731:free"

    config.provider = "default"
    assert config.model == "cohere/north-mini-code:free"
    config.provider = "openrouter"
    assert config.model == "deepseek/deepseek-v4-flash-0731:free"
    config.provider = "ollama"
    assert config.model == ""  # untouched, never seeded from elsewhere


# --- .env parsing ------------------------------------------------------------

def test_parse_dotenv_handles_comments_quotes_and_export() -> None:
    text = (
        "# a comment\n"
        "\n"
        "PLAIN=value\n"
        "QUOTED=\"hello world\"\n"
        "SINGLE='single value'\n"
        "export EXPORTED=exported-value\n"
        "INLINE=value # trailing comment\n"
        "EMPTY=\n"
        "NOEQUALS\n"
        "1INVALID=x\n"
    )
    pairs = dict(manager.parse_dotenv(text))
    assert pairs["PLAIN"] == "value"
    assert pairs["QUOTED"] == "hello world"
    assert pairs["SINGLE"] == "single value"
    assert pairs["EXPORTED"] == "exported-value"
    assert pairs["INLINE"] == "value"
    assert "EMPTY" not in pairs
    assert "NOEQUALS" not in pairs
    assert "1INVALID" not in pairs


def test_load_dotenv_applies_but_never_overrides_real_env(
    monkeypatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SEEDCODE_DOTENV_NEW=from-file\nSEEDCODE_DOTENV_KEEP=from-file\n")
    monkeypatch.delenv("SEEDCODE_DISABLE_DOTENV", raising=False)
    monkeypatch.setenv("SEEDCODE_DOTENV_NEW", "")  # is restored on teardown
    monkeypatch.setenv("SEEDCODE_DOTENV_KEEP", "from-real-env")

    applied = manager.load_dotenv(paths=[env_file])

    import os

    assert os.environ["SEEDCODE_DOTENV_NEW"] == "from-file"
    # A real environment variable wins over the file.
    assert os.environ["SEEDCODE_DOTENV_KEEP"] == "from-real-env"
    assert "SEEDCODE_DOTENV_NEW" in applied
    assert "SEEDCODE_DOTENV_KEEP" not in applied


def test_load_dotenv_is_disabled_by_flag(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SEEDCODE_DOTENV_OFF=from-file\n")
    monkeypatch.setenv("SEEDCODE_DISABLE_DOTENV", "1")
    monkeypatch.setenv("SEEDCODE_DOTENV_OFF", "")  # restored on teardown

    assert manager.load_dotenv(paths=[env_file]) == []


def test_config_load_reads_project_env_key(monkeypatch, tmp_path: Path) -> None:
    """A project .env supplies the OpenRouter key without any manual paste."""
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=sk-or-from-dotenv\n")
    monkeypatch.delenv("SEEDCODE_DISABLE_DOTENV", raising=False)
    monkeypatch.setenv("SEEDCODE_DOTENV", str(env_file))
    monkeypatch.setenv("OPENROUTER_API_KEY", "")  # restored on teardown
    monkeypatch.setattr(manager, "config_path", lambda: tmp_path / "config.json")

    cfg = manager.load_config()

    assert cfg.get_api_key("openrouter") == "sk-or-from-dotenv"
    assert cfg.active_provider == defaults.DEFAULT_PROVIDER
    assert cfg.model == defaults.DEFAULT_MODEL
    assert cfg.is_configured()


def test_first_run_seeds_default_without_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(manager, "config_path", lambda: tmp_path / "config.json")
    cfg = manager.load_config()
    assert cfg.active_provider == defaults.DEFAULT_PROVIDER
    assert cfg.providers[defaults.DEFAULT_PROVIDER].model == defaults.DEFAULT_MODEL


def test_existing_config_is_never_overwritten_on_load(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setattr(manager, "config_path", lambda: path)
    cfg = AppConfig()
    cfg.active_provider = "ollama"
    cfg.model = "llama3.2"
    manager.save_config(cfg)

    loaded = manager.load_config()
    assert loaded.active_provider == "ollama"
    assert loaded.model == "llama3.2"


# --- command router ----------------------------------------------------------

def test_status_command_is_registered_and_runs() -> None:
    ui, ctx = _ctx()
    result = dispatch(ctx, "/status")
    assert result.handled
    assert "Runtime Status" in ui.panels


def test_status_is_case_and_whitespace_tolerant() -> None:
    ui, ctx = _ctx()
    dispatch(ctx, "   /STATUS   ")
    assert "Runtime Status" in ui.panels


def test_unknown_command_reports_an_error() -> None:
    ui, ctx = _ctx()
    dispatch(ctx, "/definitely-not-a-command")
    assert any("[Command Error]" in m for m in ui.messages)
    assert any("Run /help" in m for m in ui.messages)


def test_invalid_mode_argument_reports_syntax_error() -> None:
    ui, ctx = _ctx()
    dispatch(ctx, "/codemode maybe")
    assert any("Invalid syntax" in m for m in ui.messages)


# --- Code Mode actually activates (root-cause regression) --------------------

def test_codemode_on_actually_enables_code_mode(monkeypatch, tmp_path: Path) -> None:
    """`/codemode on` must flip real state, not just print text.

    Regression: the command called the *module* ``codemode_state`` as if it
    were the accessor function, so every enable crashed with TypeError and the
    agent silently fell back to no workspace context.
    """
    from seedcode import codemode_state as cms
    from seedcode.commands import assist as assist_cmd
    from seedcode.commands import codemode as codemode_cmd

    cms.reset()
    monkeypatch.chdir(tmp_path)
    # Keep the test hermetic: no real config writes, no desktop probing.
    monkeypatch.setattr(codemode_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "is_available", lambda: (False, "test"))
    try:
        ui, ctx = _ctx()
        dispatch(ctx, "/codemode on")
        state = cms.codemode_state()
        assert state.enabled
        assert state.workspace == tmp_path.resolve()

        # The agent must now see Code Mode as active (the fix in agent.py).
        from seedcode.core.agent import AgentEngine

        assert AgentEngine._codemode_active(None) is True

        dispatch(ctx, "/codemode off")
        assert not cms.codemode_state().enabled
    finally:
        cms.reset()


# --- UI reflects real runtime state ------------------------------------------

def test_dashboard_shows_code_mode_when_active(monkeypatch, tmp_path: Path) -> None:
    from rich.console import Console

    from seedcode import codemode_state as cms
    from seedcode.commands import assist as assist_cmd
    from seedcode.commands import codemode as codemode_cmd
    from seedcode.ui.dashboard import render_dashboard
    from seedcode.ui.theme import SEED_THEME

    cms.reset()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(codemode_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "is_available", lambda: (False, "test"))
    try:
        ui, ctx = _ctx()
        dispatch(ctx, "/codemode on")
        console = Console(
            theme=SEED_THEME, width=100, force_terminal=True, record=True
        )
        render_dashboard(console, ctx.config)
        assert "Code Mode" in console.export_text()
    finally:
        cms.reset()


def test_is_command_detection() -> None:
    assert is_command(" /help")
    assert not is_command("help")
