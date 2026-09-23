"""Provider registry: the built-in backends plus user-defined providers.

The registry is the single source of truth for which providers exist:

* **built-in** — OpenRouter and Ollama are the supported first-class choices,
  with Default, FreeModel Claude/Codex and AeroLink retained for backward
  compatibility (an existing configuration can still load, select and use
  them; they are simply no longer advertised in the default list);
* **custom** — any number of user-defined, OpenAI-compatible providers
  (``custom:<slug>``), synchronised from the saved configuration.

Everything else (engine, commands, onboarding, menu) resolves providers
through :func:`get_provider` / :data:`PROVIDERS`. Custom providers are kept in
the same mapping so every existing consumer keeps working unchanged.
"""

from __future__ import annotations

from .aerolink import AeroLinkProvider
from .base import ModelInfo, Provider, ProviderError, ValidationResult
from .custom import CustomProvider
from .default import DefaultProvider
from .freemodel import FreeModelClaudeProvider, FreeModelCodexProvider
from .ollama import OllamaProvider
from .openrouter import OpenRouterProvider
from ..models import (
    CUSTOM_PROVIDER_PREFIX,
    is_custom_provider_id,
)

# Instantiated once; per-provider state (status, client cache) lives on the
# instance and is session-only. Insertion order IS the /provider menu order.
PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        DefaultProvider(),
        OpenRouterProvider(),
        FreeModelClaudeProvider(),
        FreeModelCodexProvider(),
        AeroLinkProvider(),
        OllamaProvider(),
    )
}

#: Providers the user-facing list advertises by default (v7.2.5).
CORE_PROVIDER_IDS: tuple[str, ...] = ("openrouter", "ollama")

#: Legacy built-ins kept for backward compatibility but no longer advertised.
LEGACY_PROVIDER_IDS: frozenset[str] = frozenset(
    {"default", "freemodel_claude", "freemodel_codex", "aerolink"}
)

#: The configuration currently bound to the registry (set by
#: :func:`sync_custom_providers`), used to lazily resolve a custom provider
#: when a caller asks for one before a sync happened.
_BOUND_CONFIG = None


def sync_custom_providers(config) -> None:
    """Add/update/remove the custom providers defined by ``config``.

    Call this after loading configuration and after every custom-provider
    edit, so :func:`get_provider` always reflects what is saved. Built-in
    providers are never touched.
    """
    global _BOUND_CONFIG
    _BOUND_CONFIG = config
    if config is None:
        return
    wanted = {entry.id.lower(): entry for entry in config.custom_providers}
    for pid in [p for p in PROVIDERS if is_custom_provider_id(p)]:
        if pid not in wanted:
            PROVIDERS.pop(pid, None)
    for pid, entry in wanted.items():
        existing = PROVIDERS.get(pid)
        stale = (
            existing is None
            or not isinstance(existing, CustomProvider)
            or existing.base_url != (entry.base_url or "").rstrip("/")
            or existing.label != (entry.name or entry.id)
        )
        if stale:
            PROVIDERS[pid] = CustomProvider.from_config(entry)


def get_provider(provider_id: str) -> Provider:
    """Resolve a provider by id, raising a friendly error for unknown ids."""
    pid = (provider_id or "").lower()
    provider = PROVIDERS.get(pid)
    if provider is None and is_custom_provider_id(pid) and _BOUND_CONFIG is not None:
        entry = _BOUND_CONFIG.custom_provider(pid)
        if entry is not None:
            provider = CustomProvider.from_config(entry)
            PROVIDERS[pid] = provider
    if provider is None:
        known = ", ".join(sorted(PROVIDERS))
        raise ProviderError(
            f"Unknown provider '{provider_id}'. Choose one of: {known} (see /provider)."
        )
    return provider


def provider_label(provider_id: str) -> str:
    """Display label for a provider id; safe on unset/unknown ids."""
    provider = PROVIDERS.get((provider_id or "").lower())
    return provider.label if provider else (provider_id or "(not set)")


def provider_ready(provider_id: str, config) -> bool:
    """True when a provider has what it needs to be selected: see ``requires_key``.

    The single source of truth for the provider-specific API-key rule, shared
    by the dashboard, the main menu, and /status so no surface disagrees:
    key-less providers (Default, Ollama) need none; every other provider —
    including each custom configuration — needs its own.
    """
    provider = PROVIDERS.get((provider_id or "").lower())
    if provider is None:
        return False
    return not provider.requires_key or bool(config.get_api_key(provider.id).strip())


def provider_requires_key(provider_id: str) -> bool:
    """Whether the provider takes an API key (Default and Ollama do not)."""
    provider = PROVIDERS.get((provider_id or "").lower())
    return provider.requires_key if provider is not None else True


def visible_provider_ids(config) -> list[str]:
    """The provider ids the user-facing list offers, in display order.

    OpenRouter, Ollama and every saved custom provider are advertised. A
    legacy built-in is included only while the user is actually on it, so an
    existing setup keeps working without obsolete entries cluttering the list
    for everyone else.
    """
    if config is None:
        return list(CORE_PROVIDER_IDS)
    order: list[str] = []
    active = (config.provider or "").lower()
    if active in LEGACY_PROVIDER_IDS:
        order.append(active)
    order.extend(CORE_PROVIDER_IDS)
    order.extend(entry.id.lower() for entry in config.ordered_custom_providers())
    seen: list[str] = []
    for pid in order:
        pid = (pid or "").lower()
        if pid in PROVIDERS and pid not in seen:
            seen.append(pid)
    return seen


__all__ = [
    "CORE_PROVIDER_IDS",
    "CUSTOM_PROVIDER_PREFIX",
    "LEGACY_PROVIDER_IDS",
    "ModelInfo",
    "PROVIDERS",
    "Provider",
    "ProviderError",
    "ValidationResult",
    "get_provider",
    "is_custom_provider_id",
    "provider_label",
    "provider_ready",
    "provider_requires_key",
    "sync_custom_providers",
    "visible_provider_ids",
]
