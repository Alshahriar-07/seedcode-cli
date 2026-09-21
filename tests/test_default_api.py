"""Default API configuration layer tests (v6.2.0).

No network. Verifies the credential priority (user > env > embedded), that
the embedded module is absent in source checkouts, and that config loading
applies the default without ever exposing the key value.
"""

from __future__ import annotations

import pytest

from seedcode import default_api
from seedcode.config import manager
from seedcode.core.models import AppConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env keys must not leak into these tests."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    default_api.resolve_openrouter_key.cache_clear() if hasattr(
        default_api.resolve_openrouter_key, "cache_clear"
    ) else None


def test_embedded_module_absent_in_source_checkout(monkeypatch) -> None:
    # Simulate a source checkout: no generated _default_key module
    # (the real file is git-ignored and may exist on a build machine).
    monkeypatch.setattr(default_api, "_load_embedded", lambda: ("", False, "none"))
    assert default_api.default_api_available() is False
    assert default_api.embedded_default_key() == ""
    assert default_api.is_default_only("user") is False


def test_priority_user_beats_env_beats_embedded(monkeypatch) -> None:
    monkeypatch.setattr(
        default_api, "_load_embedded", lambda: ("embedded-key", True, "note")
    )
    # Explicit user key always wins.
    assert default_api.resolve_openrouter_key("user-key", "env-key") == ("user-key", "user")
    # Environment wins over the embedded default.
    assert default_api.resolve_openrouter_key("", "env-key") == ("env-key", "env")
    # Embedded default only when nothing else is set.
    key, source = default_api.resolve_openrouter_key("", "")
    assert key == "embedded-key"
    assert source == "embedded"
    assert default_api.is_default_only(source)


def test_no_key_anywhere(monkeypatch) -> None:
    monkeypatch.setattr(default_api, "_load_embedded", lambda: ("", False, "none"))
    assert default_api.resolve_openrouter_key("", "") == ("", "none")


def test_whitespace_only_keys_are_ignored(monkeypatch) -> None:
    monkeypatch.setattr(
        default_api, "_load_embedded", lambda: ("embedded-key", True, "note")
    )
    key, source = default_api.resolve_openrouter_key("   ", "  ")
    assert source == "embedded"


def test_config_load_calls_the_default_api_hook(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(manager, "config_path", lambda: tmp_path / "config.json")
    calls: list[object] = []
    monkeypatch.setattr(manager, "_apply_default_api", lambda cfg: calls.append(cfg))

    config = manager.load_config()

    assert calls == [config]


def test_embedded_default_is_not_written_into_the_openrouter_slot(
    monkeypatch, tmp_path
) -> None:
    """The built-in credential belongs to Default only — never to OpenRouter.

    Regression guard for the v6.2.5 separation: copying the embedded key into
    OpenRouter's stored slot meant choosing OpenRouter silently inherited Seed
    Code's built-in credential.
    """
    monkeypatch.setattr(manager, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(
        default_api, "_load_embedded", lambda: ("builtin-key", True, "release")
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    config = manager.load_config()

    assert config.get_api_key("openrouter") == ""
    assert config.get_api_key("default") == ""  # resolved per request, not stored
    assert default_api.resolve_builtin_key() == ("builtin-key", "embedded")


def test_stored_user_key_survives_default_application(monkeypatch, tmp_path) -> None:
    """A stored user key is never overwritten by the embedded default."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(manager, "config_path", lambda: path)
    cfg = AppConfig()
    cfg.set_api_key("openrouter", "sk-or-real-user-key")
    cfg.model = "some-model"
    cfg.active_provider = "openrouter"
    manager.save_config(cfg)

    # A no-op default applier must leave the user key untouched.
    monkeypatch.setattr(manager, "_apply_default_api", lambda cfg: None)
    loaded = manager.load_config()
    assert loaded.get_api_key("openrouter") == "sk-or-real-user-key"


def test_apply_default_api_writes_no_provider_slot(monkeypatch) -> None:
    """`_apply_default_api` only reports availability — it stores nothing.

    The built-in credential is resolved by the Default provider per request,
    so no provider's stored configuration is ever touched from here.
    """
    monkeypatch.setattr(
        default_api, "_load_embedded", lambda: ("builtin-key", True, "release")
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-value")
    config = AppConfig()

    manager._apply_default_api(config)

    assert all(entry.api_key == "" for entry in config.providers.values())


def test_apply_default_api_keeps_stored_keys(monkeypatch) -> None:
    config = AppConfig()
    config.set_api_key("openrouter", "stored-user")
    config.set_api_key("default", "stored-default")
    manager._apply_default_api(config)
    assert config.get_api_key("openrouter") == "stored-user"
    assert config.get_api_key("default") == "stored-default"


def test_openrouter_env_var_still_configures_openrouter(monkeypatch, tmp_path) -> None:
    """The documented OpenRouter environment key keeps working where it belongs."""
    monkeypatch.setattr(manager, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-value")

    config = manager.load_config()

    assert config.get_api_key("openrouter") == "env-value"
