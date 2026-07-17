"""Provider registry and validation tests: no network required.

Empty and malformed keys are rejected before any HTTP request is made, so these
run offline.
"""

from __future__ import annotations

from seedcode.core.providers import PROVIDERS, ProviderError, get_provider
from seedcode.core.providers.base import ValidationResult
from seedcode.core.providers.freemodel import AUTO_MODEL


def test_registry_has_exactly_four_providers() -> None:
    assert set(PROVIDERS) == {"openrouter", "freemodel", "aerolink", "ollama"}


def test_providers_are_fully_independent() -> None:
    # Distinct instances, own identity, own session status slot.
    instances = list(PROVIDERS.values())
    assert len({id(p) for p in instances}) == len(instances)
    assert all(p.status == "Not Checked" for p in instances)


def test_get_provider_is_case_insensitive() -> None:
    assert get_provider("OpenRouter").id == "openrouter"
    assert get_provider("FreeModel").id == "freemodel"
    assert get_provider("OLLAMA").id == "ollama"


def test_get_provider_rejects_unknown() -> None:
    try:
        get_provider("nope")
    except ProviderError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("expected ProviderError")


def test_key_providers_reject_empty_key_offline() -> None:
    # Empty = missing key (no request possible); anything else requires a
    # REAL API request — no offline heuristics, so nothing else is testable
    # without network.
    for pid in ("openrouter", "freemodel", "aerolink"):
        result = PROVIDERS[pid].validate_key("")
        assert isinstance(result, ValidationResult)
        assert not result.ok


def test_freemodel_auto_sentinel() -> None:
    assert AUTO_MODEL == "auto"


def test_openrouter_mode_setting_offline() -> None:
    from seedcode.core.models import AppConfig

    cfg = AppConfig()
    provider = PROVIDERS["openrouter"]
    assert provider.mode(cfg) == "free"  # safe default
    ok, _ = provider.set_extra_setting(cfg, "mode", "pro")
    assert ok and provider.mode(cfg) == "pro"
    ok, _ = provider.set_extra_setting(cfg, "mode", "nonsense")
    assert not ok and provider.mode(cfg) == "pro"  # unchanged on bad input


def test_freemodel_backend_switch_keeps_models_offline() -> None:
    from seedcode.core.models import AppConfig
    from seedcode.core.providers.freemodel import _CLAUDE_BASE, _CODEX_BASE

    cfg = AppConfig()  # active provider is freemodel by default
    provider = PROVIDERS["freemodel"]
    assert provider.backend(cfg) == "codex"  # default backend
    assert provider.base_url == _CODEX_BASE

    cfg.model = "codex-model-1"
    ok, _ = provider.set_extra_setting(cfg, "backend", "claude")
    assert ok
    assert provider.backend(cfg) == "claude"
    assert provider.base_url == _CLAUDE_BASE
    assert cfg.model == ""  # claude backend has no model yet

    cfg.model = "claude-model-1"
    ok, _ = provider.set_extra_setting(cfg, "backend", "codex")
    assert ok
    assert cfg.model == "codex-model-1"  # each backend kept its own model
    ok, _ = provider.set_extra_setting(cfg, "backend", "claude")
    assert ok and cfg.model == "claude-model-1"


def test_aerolink_rejects_empty_key_offline() -> None:
    assert not PROVIDERS["aerolink"].validate_key("").ok


def test_ollama_needs_no_key() -> None:
    provider = PROVIDERS["ollama"]
    assert not provider.requires_key
    assert provider.validate_key("").ok
