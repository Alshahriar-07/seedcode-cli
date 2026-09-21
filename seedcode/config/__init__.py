"""Configuration system: load/save the app config and its defaults."""

from __future__ import annotations

from ..defaults import DEFAULT_BACKEND, DEFAULT_MODEL, DEFAULT_PROVIDER
from .defaults import CONFIG_FILENAME, ENV_KEYS
from .manager import (
    apply_default_selection,
    load_config,
    load_dotenv,
    parse_dotenv,
    save_config,
)

__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_BACKEND",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "ENV_KEYS",
    "apply_default_selection",
    "load_config",
    "load_dotenv",
    "parse_dotenv",
    "save_config",
]
