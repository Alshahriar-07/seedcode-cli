"""v7.2.5 distribution tests: pip first-run state, badges, metadata.

These lock in the release-critical fixes that have nothing to do with a live
provider:

* a pip install ships **no** built-in credential and no user key, so the
  application must say "Setup needed" / "No Key" — never falsely "Offline";
* the package metadata keeps Python 3.10 support and exposes the ``seedcode``
  console entry point (the primary user-facing command).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from rich.console import Console

from seedcode.core.models import AppConfig
from seedcode.core.providers.base import (
    STATUS_BAD_KEY,
    STATUS_CONNECTED,
    STATUS_NO_KEY,
    STATUS_OFFLINE,
    STATUS_UNKNOWN,
)
from seedcode.ui.badges import badge_for_status, badge_text
from seedcode.ui.dashboard import render_dashboard
from seedcode.ui.theme import SEED_THEME

ROOT = Path(__file__).resolve().parents[1]


# --- badges: a missing key is not an outage --------------------------------
def test_missing_key_is_not_reported_as_offline() -> None:
    key = badge_for_status(STATUS_NO_KEY)
    assert key == "no_key"
    text = badge_text(key)
    assert "Offline" not in text
    assert "No Key" in text


def test_true_offline_and_other_states_keep_their_meaning() -> None:
    assert badge_for_status(STATUS_OFFLINE) == "offline"
    assert badge_for_status(STATUS_CONNECTED) == "connected"
    assert badge_for_status(STATUS_BAD_KEY) == "error"
    assert badge_for_status(STATUS_UNKNOWN) == "ready"
    # An unknown status still maps to a real badge rather than crashing.
    assert badge_text(badge_for_status("something-odd")) != ""


# --- first-run: no credential must read as setup, not a broken app ----------
def _dashboard(config: AppConfig) -> str:
    console = Console(
        theme=SEED_THEME, width=100, force_terminal=True, record=True, highlight=False
    )
    render_dashboard(console, config)
    return console.export_text()


def test_fresh_install_without_a_key_says_setup_needed(monkeypatch) -> None:
    # Simulate a pip install: no embedded release credential, no env key.
    from seedcode import default_api

    monkeypatch.setattr(default_api, "_load_embedded", lambda: ("", False, "no key"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SEEDCODE_DEFAULT_API_KEY", raising=False)

    config = AppConfig(provider="default", model="cohere/north-mini-code:free")
    assert not config.is_configured()

    out = _dashboard(config)
    assert "Setup needed" in out
    assert "Offline" not in out
    # The application itself is installed and working — the provider is what
    # needs configuration.
    assert "Seed Code CLI v" in out


def test_default_provider_without_credential_reports_no_key_not_offline(monkeypatch) -> None:
    from seedcode import default_api
    from seedcode.core.providers.default import DefaultProvider

    monkeypatch.setattr(default_api, "_load_embedded", lambda: ("", False, "no key"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SEEDCODE_DEFAULT_API_KEY", raising=False)

    provider = DefaultProvider()
    status = provider.refresh_status(AppConfig(provider="default"))
    assert status == STATUS_NO_KEY
    assert badge_for_status(status) == "no_key"


# --- packaging metadata ------------------------------------------------------
def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_python_310_is_supported() -> None:
    project = _pyproject()["project"]
    requires = project["requires-python"]
    assert "3.10" in requires
    # No accidental floor above 3.10.
    assert "3.11" not in requires and "3.12" not in requires and "3.13" not in requires
    classifiers = project["classifiers"]
    assert "Programming Language :: Python :: 3.10" in classifiers


def test_package_exposes_the_seedcode_console_command() -> None:
    scripts = _pyproject()["project"]["scripts"]
    assert scripts.get("seedcode") == "seedcode.cli:main"


def test_entry_point_function_exists_and_module_entrypoint_works() -> None:
    from seedcode.cli import main

    assert callable(main)
    # ``python -m seedcode`` is the documented fallback and must stay wired.
    assert (ROOT / "seedcode" / "__main__.py").is_file()


def test_version_is_the_single_25_release() -> None:
    from seedcode import __version__

    assert __version__ == "8.1.0"
