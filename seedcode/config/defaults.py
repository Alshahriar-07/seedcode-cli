"""Configuration defaults and environment overrides.

Kept import-light so it can be referenced from anywhere without risk of an
import cycle. The product defaults themselves live in :mod:`seedcode.defaults`
(the single source of truth) and are re-exported here for convenience.
"""

from __future__ import annotations

from ..defaults import (
    DEFAULT_API_ENV,
    DEFAULT_BACKEND,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
)

# Environment variables that override stored API keys (CI / power users),
# keyed by provider id. Ollama needs no key so it has no entry. Values are
# tuples so a provider can accept several variable names if ever needed.
ENV_KEYS: dict[str, tuple[str, ...]] = {
    "openrouter": ("OPENROUTER_API_KEY",),
    # One FreeModel account key works on both FreeModel backends; each
    # provider still stores it in its OWN config slot.
    "freemodel_claude": ("FREEMODEL_API_KEY",),
    "freemodel_codex": ("FREEMODEL_API_KEY",),
    "aerolink": ("AEROLINK_API_KEY",),
}

# Filename of the JSON config document within the per-user app directory.
CONFIG_FILENAME = "config.json"

# --- .env loading -----------------------------------------------------------------
# A project-local ``.env`` is read at startup (real environment variables
# always win). Set ``SEEDCODE_DISABLE_DOTENV=1`` to turn it off (used by the
# test suite so a developer's local secrets never leak into a test run).
DOTENV_DISABLE_ENV = "SEEDCODE_DISABLE_DOTENV"
# Point at a specific file instead of the default ``./.env`` candidates.
DOTENV_PATH_ENV = "SEEDCODE_DOTENV"

__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_API_ENV",
    "DEFAULT_BACKEND",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "DOTENV_DISABLE_ENV",
    "DOTENV_PATH_ENV",
    "ENV_KEYS",
]
