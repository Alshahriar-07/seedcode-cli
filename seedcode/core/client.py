"""Deprecated module kept for import compatibility.

The OpenRouter client moved into the provider layer
(:mod:`seedcode.core.providers.openrouter`). Import from there instead.
"""

from __future__ import annotations

from .providers import ValidationResult
from .providers.openrouter import OpenRouterProvider

__all__ = ["ValidationResult", "validate_key"]


def validate_key(api_key: str) -> ValidationResult:
    """Validate an OpenRouter key (delegates to the provider)."""
    return OpenRouterProvider().validate_key(api_key)
