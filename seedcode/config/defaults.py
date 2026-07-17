"""Configuration defaults and environment overrides.

Kept import-light (no project imports) so it can be referenced from anywhere
without risk of an import cycle.
"""

from __future__ import annotations

# Environment variables that override stored API keys (CI / power users),
# keyed by provider id. Ollama needs no key so it has no entry. Values are
# tuples so a provider can accept several variable names if ever needed.
ENV_KEYS: dict[str, tuple[str, ...]] = {
    "openrouter": ("OPENROUTER_API_KEY",),
    "freemodel": ("FREEMODEL_API_KEY",),
    "aerolink": ("AEROLINK_API_KEY",),
}

# Filename of the JSON config document within the per-user app directory.
CONFIG_FILENAME = "config.json"
