"""Provider registry: the four supported AI backends.

The registry is the single source of truth for which providers exist:
OpenRouter, FreeModel, AeroLink, and Ollama. Each is fully independent —
own key slot, base URL, catalogue, client, and connection status — sharing
only the Provider chat contract. Everything else (engine, commands,
onboarding, menu) resolves providers through :func:`get_provider` /
:data:`PROVIDERS`.
"""

from __future__ import annotations

from .aerolink import AeroLinkProvider
from .base import ModelInfo, Provider, ProviderError, ValidationResult
from .freemodel import FreeModelProvider
from .ollama import OllamaProvider
from .openrouter import OpenRouterProvider

# Instantiated once; per-provider state (status, client cache) lives on the
# instance and is session-only.
PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        OpenRouterProvider(),
        FreeModelProvider(),
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


__all__ = [
    "ModelInfo",
    "PROVIDERS",
    "Provider",
    "ProviderError",
    "ValidationResult",
    "get_provider",
    "provider_label",
]
