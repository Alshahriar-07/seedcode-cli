"""Provider registry and validation tests: no network required.

Empty and malformed keys are rejected before any HTTP request is made, so these
run offline.
"""

from __future__ import annotations

from seedcode.core.providers import PROVIDERS, ProviderError, get_provider
from seedcode.core.providers.base import ValidationResult
from seedcode.core.providers.freemodel import (
    AUTO_MODEL,
    CLAUDE_BASE,
    CLAUDE_FALLBACK_MODELS,
    CODEX_BASE,
)


def test_registry_has_exactly_six_providers() -> None:
    assert set(PROVIDERS) == {
        "default",
        "openrouter",
        "freemodel_claude",
        "freemodel_codex",
        "aerolink",
        "ollama",
    }


def test_provider_menu_order() -> None:
    # Insertion order IS the /provider menu order; the zero-setup built-in
    # Default connection leads it.
    assert list(PROVIDERS) == [
        "default",
        "openrouter",
        "freemodel_claude",
        "freemodel_codex",
        "aerolink",
        "ollama",
    ]


def test_providers_are_fully_independent() -> None:
    # Distinct instances, own identity, own session status slot.
    instances = list(PROVIDERS.values())
    assert len({id(p) for p in instances}) == len(instances)
    assert all(p.status == "Not Checked" for p in instances)


def test_get_provider_is_case_insensitive() -> None:
    assert get_provider("OpenRouter").id == "openrouter"
    assert get_provider("FreeModel_Claude").id == "freemodel_claude"
    assert get_provider("FREEMODEL_CODEX").id == "freemodel_codex"
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
    for pid in ("openrouter", "freemodel_claude", "freemodel_codex", "aerolink"):
        result = PROVIDERS[pid].validate_key("")
        assert isinstance(result, ValidationResult)
        assert not result.ok


def test_freemodel_auto_sentinel() -> None:
    assert AUTO_MODEL == "auto"
    assert PROVIDERS["freemodel_claude"].supports_auto
    assert PROVIDERS["freemodel_codex"].supports_auto


def test_freemodel_claude_identity() -> None:
    provider = PROVIDERS["freemodel_claude"]
    assert provider.label == "FreeModel Claude"
    assert provider.base_url == CLAUDE_BASE == "https://cc.freemodel.dev"
    assert provider.backend_label == "Claude API"
    assert provider.requires_key


def test_freemodel_codex_identity() -> None:
    provider = PROVIDERS["freemodel_codex"]
    assert provider.label == "FreeModel Codex"
    assert provider.base_url == CODEX_BASE == "https://api.freemodel.dev"
    assert provider.backend_label == "Responses API"
    assert provider.requires_key


def test_freemodel_claude_fallback_list_is_claude_only() -> None:
    assert CLAUDE_FALLBACK_MODELS  # never empty: Claude models stay selectable
    assert all(mid.startswith("claude-") for mid, _ in CLAUDE_FALLBACK_MODELS)


def test_freemodel_claude_falls_back_offline(monkeypatch) -> None:
    # Discovery failing must yield the maintained fallback list, not an error.
    from seedcode.core.models import AppConfig

    provider = PROVIDERS["freemodel_claude"]

    def boom(api_key: str):
        raise ProviderError("catalogue down")

    monkeypatch.setattr(provider, "_fetch", boom)
    models = provider.list_models(AppConfig())
    assert [m.id for m in models] == [mid for mid, _ in CLAUDE_FALLBACK_MODELS]


def test_no_provider_shares_another_providers_model_list() -> None:
    # Every provider owns its catalogue logic. The built-in Default provider is
    # the one documented exception: it speaks OpenRouter's API as
    # infrastructure, so it reuses that request plumbing — but it must still
    # own its identity, its config slot, and its client cache.
    impls = {
        pid: p.list_models.__func__  # type: ignore[attr-defined]
        for pid, p in PROVIDERS.items()
        if pid != "default"
    }
    assert len(set(impls.values())) == len(impls)

    builtin = PROVIDERS["default"]
    assert builtin.id == "default" and builtin.label == "Default"
    # Client caches are per instance: switching one never disturbs the other.
    byok = PROVIDERS["openrouter"]
    builtin._client = object()
    assert byok._client is None
    builtin._client = None


# --- the built-in Default provider (separate from OpenRouter) ----------------


def test_default_provider_needs_no_key() -> None:
    provider = PROVIDERS["default"]
    assert not provider.requires_key
    assert not provider.local  # remote built-in connection, not local Ollama
    assert provider.backend_label == "Seed Code API"


def test_default_and_openrouter_are_separate_choices() -> None:
    """Sharing an infrastructure service must never merge the two providers."""
    builtin = PROVIDERS["default"]
    byok = PROVIDERS["openrouter"]
    assert builtin is not byok
    assert builtin.label != byok.label
    assert builtin.requires_key is False and byok.requires_key is True


def test_default_credential_never_leaks_into_another_slot(monkeypatch) -> None:
    """Default resolves its own credential; it never writes another provider's."""
    from seedcode import default_api
    from seedcode.core.models import AppConfig

    monkeypatch.setattr(
        default_api, "_load_embedded", lambda: ("builtin-key", True, "release")
    )
    cfg = AppConfig()
    cfg.active_provider = "default"
    provider = PROVIDERS["default"]

    key, source = provider.resolve_key(cfg)
    assert (key, source) == ("builtin-key", "embedded")
    # Nothing was copied into any stored slot.
    assert all(entry.api_key == "" for entry in cfg.providers.values())


def test_default_own_slot_wins_over_the_builtin(monkeypatch) -> None:
    from seedcode import default_api
    from seedcode.core.models import AppConfig

    monkeypatch.setattr(
        default_api, "_load_embedded", lambda: ("builtin-key", True, "release")
    )
    cfg = AppConfig()
    cfg.set_api_key("default", "my-own-key")
    assert PROVIDERS["default"].resolve_key(cfg) == ("my-own-key", "stored")


def test_default_reports_unavailable_without_a_credential(monkeypatch) -> None:
    from seedcode import default_api
    from seedcode.core.models import AppConfig

    monkeypatch.setattr(default_api, "_load_embedded", lambda: ("", False, "none"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SEEDCODE_DEFAULT_API_KEY", raising=False)

    provider = PROVIDERS["default"]
    cfg = AppConfig()
    assert provider.validate_key("").ok is False
    assert provider.detect(cfg) is False
    assert "OpenRouter" in provider.unavailable_hint(cfg)


def test_default_has_no_user_facing_settings() -> None:
    from seedcode.core.models import AppConfig

    provider = PROVIDERS["default"]
    cfg = AppConfig()
    assert provider.extra_settings(cfg) == {}
    ok, _ = provider.set_extra_setting(cfg, "mode", "pro")
    assert not ok
    assert provider.mode(cfg) == "free"


def test_ollama_is_the_local_provider() -> None:
    assert PROVIDERS["ollama"].local
    assert not PROVIDERS["openrouter"].local


def test_openrouter_mode_setting_offline() -> None:
    from seedcode.core.models import AppConfig

    cfg = AppConfig()
    provider = PROVIDERS["openrouter"]
    assert provider.mode(cfg) == "free"  # safe default
    ok, _ = provider.set_extra_setting(cfg, "mode", "pro")
    assert ok and provider.mode(cfg) == "pro"
    ok, _ = provider.set_extra_setting(cfg, "mode", "nonsense")
    assert not ok and provider.mode(cfg) == "pro"  # unchanged on bad input


def test_aerolink_rejects_empty_key_offline() -> None:
    assert not PROVIDERS["aerolink"].validate_key("").ok


def test_ollama_needs_no_key() -> None:
    provider = PROVIDERS["ollama"]
    assert not provider.requires_key
    assert provider.validate_key("").ok
