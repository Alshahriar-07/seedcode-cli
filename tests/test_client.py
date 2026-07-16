"""Provider registry and validation tests: no network required.

Empty and malformed keys are rejected before any HTTP request is made, so these
run offline.
"""

from __future__ import annotations

from seedcode.core.providers import PROVIDERS, ProviderError, get_provider
from seedcode.core.providers.base import ValidationResult


def test_registry_has_exactly_three_providers() -> None:
    assert set(PROVIDERS) == {"openrouter", "aerolink", "ollama"}


def test_get_provider_is_case_insensitive() -> None:
    assert get_provider("OpenRouter").id == "openrouter"
    assert get_provider("OLLAMA").id == "ollama"


def test_get_provider_rejects_unknown() -> None:
    try:
        get_provider("nope")
    except ProviderError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("expected ProviderError")


def test_openrouter_rejects_empty_key_offline() -> None:
    result = PROVIDERS["openrouter"].validate_key("")
    assert isinstance(result, ValidationResult)
    assert not result.ok


def test_openrouter_rejects_bad_prefix_offline() -> None:
    result = PROVIDERS["openrouter"].validate_key("not-an-openrouter-key")
    assert not result.ok


def test_aerolink_rejects_empty_key_offline() -> None:
    assert not PROVIDERS["aerolink"].validate_key("").ok


def test_ollama_needs_no_key() -> None:
    provider = PROVIDERS["ollama"]
    assert not provider.requires_key
    assert provider.validate_key("").ok
