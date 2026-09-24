"""Provider × mode matrix (v6.2.5 release requirement).

Offline checks for the release requirement that:

* every supported provider is a separate, independently configured choice;
* the API-key rule is provider-specific (Default and Ollama need no key;
  OpenRouter, FreeModel Claude/Codex and AeroLink each need their own);
* every provider works in every mode (chat / agent / code) without the UI
  or the mode label raising;
* switching across all providers keeps every provider's key and model
  isolated, so no key can leak from one to another;
* an unsupported provider or an unreachable catalogue surfaces as a clear
  message instead of a crash.

No network: provider HTTP calls are never issued here (listing failures are
simulated).
"""

from __future__ import annotations

import pytest
from contextlib import contextmanager

from seedcode.core.models import AppConfig
from seedcode.core.providers import (
    PROVIDERS,
    ProviderError,
    get_provider,
    provider_label,
    provider_ready,
    provider_requires_key,
)

PROVIDER_IDS = tuple(PROVIDERS)
KEYLESS = ("default", "ollama")
BYOK = tuple(pid for pid in PROVIDER_IDS if pid not in KEYLESS)

MODES = ("chat", "agent", "code")
_MODE_LABEL = {"chat": "Chat Mode", "agent": "Agent Mode", "code": "Code Mode"}


@pytest.fixture(autouse=True)
def _no_builtin_credential(monkeypatch):
    """Hermetic: no ambient built-in credential is available to these tests."""
    from seedcode import default_api

    monkeypatch.setattr(default_api, "_load_embedded", lambda: ("", False, "none"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SEEDCODE_DEFAULT_API_KEY", raising=False)


class _StubUI:
    """Records messages; provides a no-op spinner context."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.errors: list[str] = []

    def _record(self, message) -> None:
        self.messages.append(str(message))

    info = dim = success = warning = _record

    def error(self, message) -> None:
        self.errors.append(str(message))
        self.messages.append(str(message))

    @contextmanager
    def thinking(self, label: str = "Thinking"):
        yield


# --- one provider, one identity ---------------------------------------------
@pytest.mark.parametrize("provider_id", PROVIDER_IDS)
def test_every_provider_has_its_own_identity(provider_id: str) -> None:
    provider = PROVIDERS[provider_id]
    assert provider.id == provider_id
    assert provider.label
    assert len({p.label for p in PROVIDERS.values()}) == len(PROVIDERS)


def test_default_and_openrouter_are_distinct_entries() -> None:
    """They may share infrastructure; they must not merge as one choice."""
    assert "default" in PROVIDERS and "openrouter" in PROVIDERS
    assert PROVIDERS["default"].label == "Default"
    assert PROVIDERS["openrouter"].label == "OpenRouter"
    assert PROVIDERS["default"] is not PROVIDERS["openrouter"]


# --- provider-specific API-key rules ----------------------------------------
@pytest.mark.parametrize("provider_id", PROVIDER_IDS)
def test_api_key_requirement_is_provider_specific(provider_id: str) -> None:
    cfg = AppConfig(provider=provider_id, model="test-model")
    provider = PROVIDERS[provider_id]

    if provider_id in KEYLESS:
        assert not provider.requires_key
        assert not provider_requires_key(provider_id)
        assert cfg.get_api_key() == ""  # nothing is demanded or stored
        return

    assert provider.requires_key
    assert provider_requires_key(provider_id)
    assert not provider_ready(provider_id, cfg)  # needs its own key first
    cfg.set_api_key(provider_id, "provider-own-key")
    assert provider_ready(provider_id, cfg)


@pytest.mark.parametrize("provider_id", KEYLESS)
def test_keyless_providers_never_borrow_a_key(provider_id: str) -> None:
    cfg = AppConfig(provider=provider_id, model="test-model")
    for other in BYOK:
        cfg.set_api_key(other, f"key-{other}")
    cfg.provider = provider_id
    assert cfg.get_api_key() == ""  # Default/Ollama read their own slot only


# --- provider × mode matrix -------------------------------------------------
@pytest.mark.parametrize("provider_id", PROVIDER_IDS)
@pytest.mark.parametrize("mode", MODES)
def test_every_provider_works_in_every_mode(monkeypatch, provider_id: str, mode: str) -> None:
    """Every provider renders and labels correctly in every mode."""
    import types

    from rich.console import Console

    from seedcode import codemode_state as cms
    from seedcode.commands import status as status_cmd
    from seedcode.commands.status import mode_label
    from seedcode.ui.dashboard import render_dashboard
    from seedcode.ui.theme import SEED_THEME

    # Patch BOTH bindings: status.py imported the accessor by name, while the
    # dashboard resolves it lazily from the module.
    stub = types.SimpleNamespace(enabled=(mode == "code"), workspace=None, store=None)
    monkeypatch.setattr(cms, "codemode_state", lambda: stub)
    monkeypatch.setattr(status_cmd, "codemode_state", lambda: stub)

    cfg = AppConfig(provider=provider_id, model="test-model")
    cfg.agent_mode = mode in ("agent", "code")

    assert mode_label(cfg) == _MODE_LABEL[mode]

    console = Console(theme=SEED_THEME, width=100, force_terminal=True, record=True)
    render_dashboard(console, cfg)  # must never raise
    text = console.export_text()
    assert provider_label(provider_id) in text or "Not configured" in text
    # The API-key row follows the provider's real requirement, in every mode.
    assert ("API Key" in text) is provider_requires_key(provider_id)


def test_switching_provider_does_not_leak_key_or_model() -> None:
    """Default → OpenRouter → FreeModel → Ollama → Default, end to end."""
    cfg = AppConfig()
    order = ("default", "openrouter", "freemodel_claude", "freemodel_codex", "aerolink", "ollama")
    for pid in order:
        cfg.provider = pid
        cfg.set_api_key(pid, f"key-{pid}")
        cfg.model = f"model-{pid}"

    for pid in order:
        cfg.provider = pid
        assert cfg.model == f"model-{pid}"
        assert cfg.get_api_key() == f"key-{pid}"

    # Every slot still holds exactly its own values.
    for pid in order:
        entry = cfg.providers[pid]
        assert entry.api_key == f"key-{pid}"
        assert entry.model == f"model-{pid}"

    # And the persisted shape round-trips without cross-contamination.
    restored = AppConfig.model_validate(cfg.model_dump())
    for pid in order:
        assert restored.providers[pid].api_key == f"key-{pid}"
        assert restored.providers[pid].model == f"model-{pid}"


# --- clear errors instead of crashes ---------------------------------------
def test_unknown_provider_is_a_clear_error() -> None:
    with pytest.raises(ProviderError) as info:
        get_provider("does-not-exist")
    message = str(info.value)
    assert "does-not-exist" in message
    assert "/provider" in message


@pytest.mark.parametrize("provider_id", BYOK)
def test_catalogue_failure_is_reported_not_raised(monkeypatch, provider_id: str) -> None:
    """A provider/model problem reaches the user as a message, never a crash."""
    from seedcode.commands.provider import select_model

    cfg = AppConfig(provider=provider_id, model="some-model")
    cfg.set_api_key(provider_id, "own-key")

    def boom(_config):
        raise ProviderError(f"{provider_id} catalogue is unreachable")

    monkeypatch.setattr(PROVIDERS[provider_id], "list_models", boom)

    ui = _StubUI()
    select_model(ui, cfg)  # must not raise

    if provider_id == "aerolink":
        # AeroLink may not expose a catalogue; the typed id is accepted as-is.
        assert cfg.model == "some-model"
    else:
        assert any("unreachable" in message for message in ui.errors)
