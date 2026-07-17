"""Config model tests: no network, no API key required."""

from __future__ import annotations

from seedcode.core.models import DEFAULT_MAX_TOKENS, AppConfig


def test_config_defaults() -> None:
    cfg = AppConfig()
    assert cfg.model == ""  # models are never hardcoded
    assert cfg.provider == "freemodel"
    assert cfg.max_tokens == DEFAULT_MAX_TOKENS
    assert not cfg.is_configured()


def test_max_tokens_clamped() -> None:
    cfg = AppConfig(model="some/model", max_tokens=65536)
    assert cfg.effective_max_tokens() == 4096  # never send huge budgets
    cfg.max_tokens = 0
    assert cfg.effective_max_tokens() == 1
    cfg.max_tokens = 2048
    assert cfg.effective_max_tokens() == 2048


def test_free_models_capped_at_default() -> None:
    cfg = AppConfig(model="meta-llama/llama-3-8b:free", max_tokens=4096)
    assert cfg.effective_max_tokens() == DEFAULT_MAX_TOKENS


def test_per_provider_keys() -> None:
    cfg = AppConfig()
    cfg.set_api_key("freemodel", "fe_oa_abc")
    cfg.set_api_key("aerolink", "al-key")
    assert cfg.get_api_key("freemodel") == "fe_oa_abc"
    assert cfg.get_api_key() == "fe_oa_abc"  # active provider default
    cfg.provider = "aerolink"
    assert cfg.get_api_key() == "al-key"


def test_is_configured_per_provider() -> None:
    cfg = AppConfig(model="some/model")
    assert not cfg.is_configured()  # freemodel without key
    cfg.set_api_key("freemodel", "fe_oa_abc")
    assert cfg.is_configured()
    cfg = AppConfig(provider="ollama", model="llama3.2")
    assert cfg.is_configured()  # ollama never needs a key


def test_model_memory_per_provider() -> None:
    cfg = AppConfig(model="a/b")
    cfg.provider = "ollama"
    assert cfg.recall_model() == ""
    cfg.model = "llama3.2"
    cfg.provider = "freemodel"
    assert cfg.recall_model() == "a/b"


def test_legacy_config_migrates() -> None:
    # v0.x flat key + display-name provider (OpenRouter was the original
    # backend, and is a first-class provider again).
    cfg = AppConfig.model_validate(
        {"api_key": "sk-or-old", "provider": "OpenRouter", "model": "x/y"}
    )
    assert cfg.provider == "openrouter"
    assert cfg.get_api_key("openrouter") == "sk-or-old"
    assert cfg.providers["openrouter"].model == "x/y"


def test_v1_config_migrates_to_nested_providers() -> None:
    cfg = AppConfig.model_validate(
        {
            "provider": "aerolink",
            "model": "claude-x",
            "api_keys": {"openrouter": "sk-or-abc", "aerolink": "al-key"},
            "models": {"openrouter": "a/b"},
        }
    )
    assert cfg.active_provider == "aerolink"
    assert cfg.providers["aerolink"].model == "claude-x"
    assert cfg.providers["aerolink"].api_key == "al-key"
    assert cfg.providers["openrouter"].model == "a/b"
    assert cfg.providers["openrouter"].api_key == "sk-or-abc"


def test_stored_shape_is_nested() -> None:
    dumped = AppConfig().model_dump()
    assert dumped["active_provider"] == "freemodel"
    assert set(dumped["providers"]) >= {"openrouter", "freemodel", "aerolink", "ollama"}
    assert dumped["providers"]["freemodel"] == {"api_key": "", "model": "", "options": {}}
    # Round-trips losslessly.
    cfg = AppConfig(model="a/b")
    cfg.set_api_key("aerolink", "al-key")
    cfg.provider_options("openrouter")["mode"] = "pro"
    assert AppConfig.model_validate(cfg.model_dump()) == cfg


def test_four_provider_keys_are_isolated() -> None:
    cfg = AppConfig()
    cfg.set_api_key("openrouter", "sk-or-1")
    cfg.set_api_key("freemodel", "fe_oa_2")
    cfg.set_api_key("aerolink", "al-3")
    assert cfg.get_api_key("openrouter") == "sk-or-1"
    assert cfg.get_api_key("freemodel") == "fe_oa_2"
    assert cfg.get_api_key("aerolink") == "al-3"
    assert cfg.get_api_key("ollama") == ""


def test_switching_never_overwrites_other_providers() -> None:
    cfg = AppConfig(model="free/one")
    cfg.set_api_key("freemodel", "fe_oa_abc")
    cfg.provider = "aerolink"
    cfg.set_api_key("aerolink", "al-key")
    cfg.model = "claude-x"
    cfg.provider = "ollama"
    cfg.model = "llama3.2"
    # Every provider kept its own key and model.
    assert cfg.providers["freemodel"].api_key == "fe_oa_abc"
    assert cfg.providers["freemodel"].model == "free/one"
    assert cfg.providers["aerolink"].api_key == "al-key"
    assert cfg.providers["aerolink"].model == "claude-x"
    assert cfg.providers["ollama"].model == "llama3.2"
    # And switching back restores instantly.
    cfg.provider = "freemodel"
    assert cfg.model == "free/one"
    assert cfg.get_api_key() == "fe_oa_abc"


def test_masked_key() -> None:
    assert AppConfig().masked_key() == "(not set)"
    cfg = AppConfig()
    cfg.set_api_key("freemodel", "fe_oa_1234567890abcdef")
    masked = cfg.masked_key("freemodel")
    assert masked.startswith("fe_oa_12") and masked.endswith("cdef")
    assert "..." in masked
