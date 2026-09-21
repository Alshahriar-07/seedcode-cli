"""Provider registry: the six supported AI backends.

The registry is the single source of truth for which providers exist:
**Default** (Seed Code's built-in connection — no user API key), OpenRouter,
FreeModel Claude, FreeModel Codex, AeroLink, and Ollama. Each is fully
independent — own key slot, base URL, catalogue, client, and connection
status — sharing only the Provider chat contract. Default and OpenRouter may
use the same underlying service, but they are separate providers everywhere:
separate config, separate model, separate status, separate credential.
Everything else (engine, commands, onboarding, menu) resolves providers
through :func:`get_provider` / :data:`PROVIDERS`.
"""

from __future__ import annotations

from .aerolink import AeroLinkProvider
from .base import ModelInfo, Provider, ProviderError, ValidationResult
from .default import DefaultProvider
from .freemodel import FreeModelClaudeProvider, FreeModelCodexProvider
from .ollama import OllamaProvider
from .openrouter import OpenRouterProvider

# Instantiated once; per-provider state (status, client cache) lives on the
# instance and is session-only. Insertion order IS the /provider menu order,
# and Default leads it: it is the zero-setup option.
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


def get_provider(provider_id: str) -> Provider:
    """Resolve a provider by id, raising a friendly error for unknown ids."""
    provider = PROVIDERS.get((provider_id or "").lower())
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
    Default and Ollama need no key, every other provider needs its own.
    """
    provider = PROVIDERS.get((provider_id or "").lower())
    if provider is None:
        return False
    return not provider.requires_key or bool(config.get_api_key(provider.id).strip())


def provider_requires_key(provider_id: str) -> bool:
    """Whether the provider takes an API key (Default and Ollama do not)."""
    provider = PROVIDERS.get((provider_id or "").lower())
    return provider.requires_key if provider is not None else True


__all__ = [
    "ModelInfo",
    "PROVIDERS",
    "Provider",
    "ProviderError",
    "ValidationResult",
    "get_provider",
    "provider_label",
    "provider_ready",
    "provider_requires_key",
]
