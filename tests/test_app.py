"""Menu status and provider pure-function tests: no network required."""

from __future__ import annotations

from seedcode.app import _model_status, _provider_status
from seedcode.core.models import AppConfig
from seedcode.core.providers.freemodel import AUTO_MODEL
from seedcode.core.providers.openrouter import _entry_is_free


def test_provider_status_not_configured() -> None:
    # A key-requiring provider with no key is not ready.
    cfg = AppConfig(provider="openrouter")
    assert _provider_status(cfg) == "Not Configured"


def test_provider_status_ready_with_key() -> None:
    cfg = AppConfig(provider="freemodel_claude")
    cfg.set_api_key("freemodel_claude", "fe_oa_abc")
    assert _provider_status(cfg) == "FreeModel Claude"
    cfg.provider = "freemodel_codex"
    cfg.set_api_key("freemodel_codex", "fe_oa_abc")
    assert _provider_status(cfg) == "FreeModel Codex"


def test_provider_status_ollama_needs_no_key() -> None:
    cfg = AppConfig(provider="ollama")
    assert _provider_status(cfg) == "Ollama"


def test_provider_status_default_needs_no_user_key() -> None:
    # The built-in Default provider is selectable without any API key.
    cfg = AppConfig(provider="default")
    assert _provider_status(cfg) == "Default"


def test_model_status() -> None:
    cfg = AppConfig()
    assert _model_status(cfg) == "Not Selected"
    cfg.model = AUTO_MODEL
    assert _model_status(cfg) == "Auto (best free model)"
    cfg.model = "vendor/model"
    assert _model_status(cfg) == "vendor/model"


def test_openrouter_free_tagging_requires_zero_pricing() -> None:
    assert _entry_is_free({"pricing": {"prompt": "0", "completion": "0"}})
    assert not _entry_is_free({"pricing": {"prompt": "0.001", "completion": "0"}})
    assert not _entry_is_free({"pricing": {"prompt": "0", "completion": "0.002"}})
    assert not _entry_is_free({"pricing": {}})  # unknown pricing is NOT free
    assert not _entry_is_free({})
    assert not _entry_is_free({"pricing": {"prompt": "zero", "completion": "0"}})
