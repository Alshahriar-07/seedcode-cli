"""Config model tests: no network, no API key required."""

from __future__ import annotations

from seedcode.core.models import DEFAULT_MAX_TOKENS, AppConfig


def test_config_defaults() -> None:
    cfg = AppConfig()
    assert cfg.model == ""  # models are never hardcoded
    assert cfg.provider == "openrouter"
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
    cfg.set_api_key("openrouter", "sk-or-abc")
    cfg.set_api_key("aerolink", "al-key")
    assert cfg.get_api_key("openrouter") == "sk-or-abc"
    assert cfg.get_api_key() == "sk-or-abc"  # active provider default
    cfg.provider = "aerolink"
    assert cfg.get_api_key() == "al-key"


def test_is_configured_per_provider() -> None:
    cfg = AppConfig(model="some/model")
    assert not cfg.is_configured()  # openrouter without key
    cfg.set_api_key("openrouter", "sk-or-abc")
    assert cfg.is_configured()
    cfg = AppConfig(provider="ollama", model="llama3.2")
    assert cfg.is_configured()  # ollama never needs a key


def test_model_memory_per_provider() -> None:
    cfg = AppConfig(model="a/b")
    cfg.remember_model()
    cfg.provider = "ollama"
    assert cfg.recall_model() == ""
    cfg.model = "llama3.2"
    cfg.remember_model()
    cfg.provider = "openrouter"
    assert cfg.recall_model() == "a/b"


def test_legacy_config_migrates() -> None:
    cfg = AppConfig.model_validate(
        {"api_key": "sk-or-old", "provider": "OpenRouter", "model": "x/y"}
    )
    assert cfg.provider == "openrouter"
    assert cfg.get_api_key("openrouter") == "sk-or-old"


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
    assert dumped["active_provider"] == "openrouter"
    assert set(dumped["providers"]) >= {"openrouter", "aerolink", "ollama"}
    assert dumped["providers"]["openrouter"] == {"api_key": "", "model": ""}
    # Round-trips losslessly.
    cfg = AppConfig(model="a/b")
    cfg.set_api_key("aerolink", "al-key")
    assert AppConfig.model_validate(cfg.model_dump()) == cfg


def test_switching_never_overwrites_other_providers() -> None:
    cfg = AppConfig(model="free/one:free")
    cfg.set_api_key("openrouter", "sk-or-abc")
    cfg.provider = "aerolink"
    cfg.set_api_key("aerolink", "al-key")
    cfg.model = "claude-x"
    cfg.provider = "ollama"
    cfg.model = "llama3.2"
    # Every provider kept its own key and model.
    assert cfg.providers["openrouter"].api_key == "sk-or-abc"
    assert cfg.providers["openrouter"].model == "free/one:free"
    assert cfg.providers["aerolink"].api_key == "al-key"
    assert cfg.providers["aerolink"].model == "claude-x"
    assert cfg.providers["ollama"].model == "llama3.2"
    # And switching back restores instantly.
    cfg.provider = "openrouter"
    assert cfg.model == "free/one:free"
    assert cfg.get_api_key() == "sk-or-abc"


def test_masked_key() -> None:
    assert AppConfig().masked_key() == "(not set)"
    cfg = AppConfig()
    cfg.set_api_key("openrouter", "sk-or-1234567890abcdef")
    masked = cfg.masked_key("openrouter")
    assert masked.startswith("sk-or-12") and masked.endswith("cdef")
    assert "..." in masked
