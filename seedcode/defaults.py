"""Authoritative Seed Code defaults — the single source of truth.

Nothing else may hardcode the default provider, backend, or model. Every
consumer (config loading, onboarding, provider selection, the UI) imports
these values, so changing the shipped default is a one-line edit here.

Kept deliberately import-light (no project imports) so it can be imported
from anywhere — including :mod:`seedcode.core.models` — without an import
cycle.
"""

from __future__ import annotations

# The provider Seed Code ships with: the built-in Default connection, which
# needs no API key from the user (see seedcode.core.providers.default). It is
# a first-class provider, separate from OpenRouter in the UI and in config.
# A fresh install therefore needs no key AND no model choice: it ships with
# DEFAULT_MODEL below and is ready to work immediately.
DEFAULT_PROVIDER = "default"

# The backend the default provider speaks to. Separate from DEFAULT_PROVIDER
# on purpose: Default presents Seed Code's own connection while routing to
# OpenRouter's OpenAI-compatible API as infrastructure.
DEFAULT_BACKEND = "openrouter"

# The model chosen on first run for the built-in Default provider: a free
# coding model, so a fresh install can work immediately without picking a paid
# one. Users can change it any time with /model — this is only the *default*
# for the Default provider, never forced onto OpenRouter, FreeModel, Ollama,
# or any other provider (each keeps its own model slot).
DEFAULT_MODEL = "cohere/north-mini-code:free"

# The environment variable that supplies the default connection's API key.
DEFAULT_API_ENV = "OPENROUTER_API_KEY"

__all__ = [
    "DEFAULT_API_ENV",
    "DEFAULT_BACKEND",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
]
