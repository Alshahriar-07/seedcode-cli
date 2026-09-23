"""v7.2.5 provider-system tests: Custom providers, health, failover, Ollama.

Everything here is offline: HTTP and processes are injected/stubbed, and no
credential is real. The tests lock in the behaviors the provider overhaul
promises:

* user-defined providers can be added/edited/enabled/reordered/deleted without
  an artificial limit, and are masked + persisted correctly;
* the user-facing provider list is OpenRouter, Ollama and the saved custom
  providers, with legacy built-ins kept only for backward compatibility;
* provider health classifies failures and enforces cooldowns (no retry loops);
* a failed request fails over to the next healthy provider while preserving
  the conversation — it does not restart the work;
* Ollama is detected and auto-started, never duplicated, with a bounded wait.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from seedcode.core import chat as chat_mod
from seedcode.core.models import (
    AppConfig,
    CustomProviderConfig,
    is_custom_provider_id,
    valid_base_url,
)
from seedcode.core.providers import (
    PROVIDERS,
    get_provider,
    health,
    provider_ready,
    sync_custom_providers,
    visible_provider_ids,
)
from seedcode.core.providers.base import (
    ModelInfo,
    Provider,
    ProviderError,
    ValidationResult,
)
from seedcode.core.providers.custom import CustomProvider
from seedcode.core.providers.failover import FailoverChain
from seedcode.core.providers.ollama_start import ensure_running


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Restore the provider registry and health after every test."""
    snapshot = dict(PROVIDERS)
    health.reset()
    yield
    PROVIDERS.clear()
    PROVIDERS.update(snapshot)
    health.reset()


# --- custom provider configuration -------------------------------------------


def _config_with_customs() -> AppConfig:
    config = AppConfig()
    config.add_custom_provider("Provider A", "https://a.example/v1", "key-a", "model-a")
    config.add_custom_provider("Provider B", "https://b.example/v1", "key-b", "model-b")
    return config


def test_add_custom_provider_builds_a_stable_unique_id() -> None:
    config = AppConfig()
    entry = config.add_custom_provider("My Provider", "https://example.com/v1", "sk-x")
    assert entry.id == "custom:my-provider"
    assert entry.label == "My Provider"
    assert is_custom_provider_id(entry.id)
    # A duplicate display name gets its own id rather than overwriting.
    second = config.add_custom_provider("My Provider", "https://example.com/v2", "sk-y")
    assert second.id != entry.id
    assert len(config.custom_providers) == 2


def test_custom_provider_ids_are_unbounded() -> None:
    config = AppConfig()
    for index in range(25):
        config.add_custom_provider(f"Provider {index}", f"https://p{index}.example/v1")
    assert len(config.custom_providers) == 25


def test_add_custom_provider_validates_input() -> None:
    config = AppConfig()
    with pytest.raises(ValueError):
        config.add_custom_provider("", "https://example.com/v1")
    with pytest.raises(ValueError):
        config.add_custom_provider("Bad URL", "ftp://example.com")
    assert config.custom_providers == []
    assert valid_base_url("https://ok.example/v1")
    assert not valid_base_url("example.com")


def test_custom_provider_keys_models_and_masking() -> None:
    config = AppConfig()
    entry = config.add_custom_provider("Provider A", "https://a.example/v1", "supersecretkey-1234", "m")
    assert config.get_api_key(entry.id) == "supersecretkey-1234"
    config.set_api_key(entry.id, "another-secret-5678")
    assert config.get_api_key(entry.id) == "another-secret-5678"
    # Masking never reveals the whole key.
    masked = config.masked_key(entry.id)
    assert "another-secret-5678" not in masked
    assert "..." in masked
    # A short key is fully obscured.
    short = config.add_custom_provider("Short", "https://s.example/v1", "abc123", "m")
    assert config.masked_key(short.id) == "******"
    # The model property routes to the custom slot.
    config.provider = entry.id
    assert config.model == "m"
    config.model = "m2"
    assert config.custom_provider(entry.id).model == "m2"


def test_custom_provider_enable_disable_reorder_and_delete() -> None:
    config = _config_with_customs()
    a, b = config.custom_providers
    assert [e.id for e in config.ordered_custom_providers()] == [a.id, b.id]

    assert config.set_custom_enabled(a.id, False)
    assert [e.id for e in config.ordered_custom_providers(enabled_only=True)] == [b.id]

    assert config.move_custom_provider(b.id, -1)
    assert [e.id for e in config.ordered_custom_providers()] == [b.id, a.id]
    # Order is contiguous after the move.
    assert [e.priority for e in config.ordered_custom_providers()] == [0, 1]

    assert config.remove_custom_provider(b.id)
    assert [e.id for e in config.custom_providers] == [a.id]


def test_removing_the_active_custom_provider_falls_back_safely() -> None:
    config = _config_with_customs()
    a = config.custom_providers[0]
    config.provider = a.id
    assert config.remove_custom_provider(a.id)
    assert config.provider == "default"
    assert config.custom_provider(a.id) is None


def test_update_custom_provider_validates_and_applies() -> None:
    config = _config_with_customs()
    a = config.custom_providers[0]
    assert config.update_custom_provider(a.id, name="Renamed", base_url="https://new.example/v2") is not None
    assert config.custom_provider(a.id).name == "Renamed"
    assert config.custom_provider(a.id).base_url == "https://new.example/v2"
    with pytest.raises(ValueError):
        config.update_custom_provider(a.id, base_url="not-a-url")


def test_custom_providers_survive_a_config_roundtrip() -> None:
    config = _config_with_customs()
    config.provider = config.custom_providers[0].id
    restored = AppConfig.model_validate(config.model_dump())
    assert len(restored.custom_providers) == 2
    assert restored.provider == config.provider
    assert restored.get_api_key(config.provider) == "key-a"


def test_known_custom_active_provider_is_not_downgraded_on_load() -> None:
    config = _config_with_customs()
    a = config.custom_providers[0]
    config.provider = a.id
    restored = AppConfig.model_validate(config.model_dump())
    assert restored.provider == a.id
    # An active custom id that is not defined falls back to the built-in default.
    raw = config.model_dump()
    raw["active_provider"] = "custom:does-not-exist"
    assert AppConfig.model_validate(raw).provider == "default"


def test_custom_provider_is_configured_only_when_usable() -> None:
    config = AppConfig()
    entry = config.add_custom_provider("A", "https://a.example/v1", "k", "m")
    config.provider = entry.id
    assert config.is_configured()
    config.set_custom_enabled(entry.id, False)
    assert not config.is_configured()


# --- registry ----------------------------------------------------------------


def test_sync_registers_and_unregisters_custom_providers() -> None:
    config = _config_with_customs()
    sync_custom_providers(config)
    a = config.custom_providers[0]
    provider = PROVIDERS[a.id]
    assert isinstance(provider, CustomProvider)
    assert provider.label == "Provider A"
    assert provider.base_url == "https://a.example/v1"
    assert get_provider(a.id) is provider

    config.remove_custom_provider(a.id)
    sync_custom_providers(config)
    assert a.id not in PROVIDERS


def test_visible_list_is_openrouter_ollama_and_customs() -> None:
    config = _config_with_customs()
    config.provider = "openrouter"
    sync_custom_providers(config)
    ids = visible_provider_ids(config)
    assert ids[:2] == ["openrouter", "ollama"]
    assert [e.id for e in config.custom_providers] == ids[2:]
    # Legacy built-ins are not advertised while unused...
    assert "default" not in ids
    # ...but stay reachable when the user is actually on one.
    config.provider = "default"
    assert visible_provider_ids(config)[0] == "default"


def test_provider_ready_uses_each_custom_providers_own_key() -> None:
    config = _config_with_customs()
    sync_custom_providers(config)
    a, b = config.custom_providers
    config.set_api_key(b.id, "")
    assert provider_ready(a.id, config)
    assert not provider_ready(b.id, config)


# --- health ------------------------------------------------------------------


def test_classify_error_maps_each_failure_kind() -> None:
    assert health.classify_error(ProviderError("Rate limited (HTTP 429)", transient=True)) is health.HealthState.RATE_LIMITED
    assert health.classify_error(ProviderError("request timed out", transient=True)) is health.HealthState.OFFLINE
    assert health.classify_error(ProviderError("server error (HTTP 500)", transient=True)) is health.HealthState.RETRYING
    assert health.classify_error(ProviderError("Authentication failed. Your key may be invalid.")) is health.HealthState.AUTHENTICATION_ERROR
    assert health.classify_error(ProviderError("unknown model")) is health.HealthState.TEMPORARILY_UNAVAILABLE


def test_health_tracker_cooldown_and_permanent_blocks() -> None:
    now = {"t": 100.0}
    tracker = health.HealthTracker(clock=lambda: now["t"])
    tracker.record_failure("p", ProviderError("boom", transient=True))
    assert not tracker.available("p")
    now["t"] += 999.0
    assert tracker.available("p")

    # A rejected key never becomes available again in this session.
    tracker.record_failure("q", ProviderError("API key was rejected"))
    now["t"] += 10_000.0
    assert not tracker.available("q")

    tracker.record_success("p")
    assert tracker.state("p") is health.HealthState.HEALTHY
    assert tracker.available("p")


def test_health_tracker_holds_no_credential_slot() -> None:
    tracker = health.HealthTracker()
    tracker.record_failure("p", ProviderError("server error (HTTP 500)", transient=True))
    record = tracker.get("p")
    # The tracker has no field that could carry an API key, and its reason is
    # a short single-line description, not a stored credential.
    assert not hasattr(record, "api_key")
    assert "\n" not in record.last_error
    assert len(record.last_error) <= 160


# --- failover ----------------------------------------------------------------


@dataclass
class _StubProvider(Provider):
    """A minimal provider whose stream behavior is scripted."""

    outcome: str = "ok"  # "ok" | "transient" | "permanent"

    def __post_init__(self) -> None:
        self.id = getattr(self, "id", "stub")
        self.label = self.id
        self.requires_key = False

    def validate_key(self, api_key: str) -> ValidationResult:
        return ValidationResult(True, "ok")

    def list_models(self, config) -> list[ModelInfo]:
        return [ModelInfo(id="m")]

    def stream_chat(self, config, messages):
        if self.outcome == "transient":
            raise ProviderError("temporary 503", transient=True)
        if self.outcome == "permanent":
            raise ProviderError("Authentication failed. Your key may be invalid.")
        yield "hello"


def _failover_config() -> AppConfig:
    config = AppConfig()
    config.active_provider = "openrouter"
    config.providers["openrouter"].model = "primary"
    config.providers["openrouter"].api_key = "k"
    config.providers["ollama"].model = "llama3.2"
    return config


def test_failover_chain_orders_active_first_then_healthy() -> None:
    config = _failover_config()
    chain = FailoverChain(config)
    ids = [c.provider_id for c in chain.candidates()]
    assert ids[0] == "openrouter"
    assert "ollama" in ids


def test_failover_chain_skips_excluded_and_cooling_down() -> None:
    config = _failover_config()
    chain = FailoverChain(config)
    assert chain.next_candidate({"openrouter"}).provider_id == "ollama"
    health.tracker().record_failure("ollama", ProviderError("down", transient=True))
    assert chain.next_candidate({"openrouter"}) is None


def test_chat_engine_fails_over_without_losing_the_conversation(monkeypatch) -> None:
    monkeypatch.setenv("SEEDCODE_MAX_RETRIES", "0")
    config = _failover_config()
    primary = _StubProvider(outcome="permanent")
    primary.id = "openrouter"
    primary.label = "OpenRouter"
    fallback = _StubProvider(outcome="ok")
    fallback.id = "ollama"
    fallback.label = "Ollama"
    monkeypatch.setitem(PROVIDERS, "openrouter", primary)
    monkeypatch.setitem(PROVIDERS, "ollama", fallback)

    events: list[tuple[str, str]] = []
    engine = chat_mod.ChatEngine(config, on_event=lambda k, d: events.append((k, d)))
    engine.add_user("do the thing")
    reply = "".join(engine.stream_reply())

    assert reply == "hello"
    assert config.provider == "ollama"  # the request resumed on the fallback
    assert engine.last_switch == ("openrouter", "ollama")
    assert any(kind == "switching_provider" for kind, _ in events)
    # The conversation was preserved, not restarted.
    assert any(m.content == "do the thing" for m in engine.messages)


def test_chat_engine_reports_failure_when_no_fallback_exists(monkeypatch) -> None:
    monkeypatch.setenv("SEEDCODE_MAX_RETRIES", "0")
    config = _failover_config()
    config.providers["ollama"].model = ""  # not a usable fallback
    primary = _StubProvider(outcome="permanent")
    primary.id = "openrouter"
    monkeypatch.setitem(PROVIDERS, "openrouter", primary)

    engine = chat_mod.ChatEngine(config)
    engine.add_user("go")
    with pytest.raises(chat_mod.ChatError):
        list(engine.stream_reply())
    assert config.provider == "openrouter"


# --- Ollama auto-start -------------------------------------------------------


def _ollama_config() -> AppConfig:
    config = AppConfig()
    config.ollama_host = "http://localhost:11434"
    return config


def test_ollama_already_running_is_not_started_again() -> None:
    spawned: list = []
    ok, message = ensure_running(
        _ollama_config(),
        detect=lambda _host: True,
        which=lambda _name: "/usr/bin/ollama",
        popen=lambda *a, **k: spawned.append(a),
    )
    assert ok
    assert "already running" in message.lower()
    assert spawned == []


def test_ollama_is_started_and_waited_for() -> None:
    state = {"up": False, "spawned": [], "clock": 0.0}
    probes = {"n": 0}

    def detect(_host: str) -> bool:
        probes["n"] += 1
        return state["up"]

    def popen(args, **kwargs):
        state["spawned"].append(args)

    def now() -> float:
        state["clock"] += 0.5
        return state["clock"]

    def sleep(_seconds: float) -> None:
        state["up"] = True  # the server comes up after the first probe

    ok, message = ensure_running(
        _ollama_config(),
        detect=detect,
        which=lambda _name: "/usr/bin/ollama",
        popen=popen,
        now=now,
        sleep=sleep,
        timeout=5.0,
    )
    assert ok, message
    assert state["spawned"] == [["/usr/bin/ollama", "serve"]]
    assert "started" in message.lower()


def test_ollama_missing_executable_reports_actionably() -> None:
    ok, message = ensure_running(
        _ollama_config(),
        detect=lambda _host: False,
        which=lambda _name: None,
    )
    assert not ok
    assert "not installed" in message.lower()


def test_ollama_timeout_is_bounded(monkeypatch) -> None:
    state = {"clock": 0.0}

    def now() -> float:
        state["clock"] += 1.0
        return state["clock"]

    ok, message = ensure_running(
        _ollama_config(),
        detect=lambda _host: False,
        which=lambda _name: "/usr/bin/ollama",
        popen=lambda *a, **k: None,
        now=now,
        sleep=lambda _s: None,
        timeout=2.0,
    )
    assert not ok
    assert "did not become ready" in message.lower()


def test_ollama_duplicate_launch_is_prevented() -> None:
    spawned: list = []
    answers = iter([False, True, True, True])

    ok, message = ensure_running(
        _ollama_config(),
        detect=lambda _host: next(answers),
        which=lambda _name: "/usr/bin/ollama",
        popen=lambda *a, **k: spawned.append(a),
    )
    assert ok
    assert spawned == []  # the second check saw it up before spawning
    assert "already running" in message.lower()
