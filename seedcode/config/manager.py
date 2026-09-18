"""Configuration loading and saving for Seed Code.

The config is a single JSON document under ``~/.seedcode/config.json``. Loading
is fault-tolerant: a missing or corrupt file yields sane defaults rather than a
crash, honouring the rule "never crash". Saving is best-effort for the same
reason (a locked or full disk must not kill the session).
"""

from __future__ import annotations

import json
import os

from ..core.models import AppConfig
from ..utils.helpers import config_path, restrict_permissions
from ..utils.logger import get_logger
from .defaults import ENV_KEYS

_log = get_logger("config")


def _apply_default_api(config: AppConfig) -> None:
    """v6.2.0 embedded default: a stored OpenRouter key wins over it.

    Priority: explicit user key > OPENROUTER_API_KEY env var > embedded
    default (release artifacts only). The embedded key is written into the
    config's OpenRouter slot like any other key so every existing consumer
    (dashboard status, validation, chat) sees a ready provider.
    """
    from ..default_api import resolve_openrouter_key

    stored = config.get_api_key("openrouter")
    env_key = ""
    for env_name in ENV_KEYS.get("openrouter", ()):
        env_key = os.environ.get(env_name, "").strip()
        if env_key:
            break
    key, source = resolve_openrouter_key(stored, env_key)
    if key and source != "user":
        config.set_api_key("openrouter", key)
        _log.info("openrouter key resolved from %s", source)


def load_config() -> AppConfig:
    """Load configuration from disk, falling back to defaults on any error."""
    path = config_path()
    config = AppConfig()

    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            config = AppConfig.model_validate(raw)
            _log.info("config loaded from %s", path)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            # Corrupt or unreadable config -> start from defaults instead of dying.
            _log.warning("config unreadable (%s); using defaults", exc)
            config = AppConfig()
    else:
        _log.info("no config file yet (first run)")

    # Explicit environment variables always win over stored API keys.
    for provider_id, env_names in ENV_KEYS.items():
        for env_name in env_names:
            env_key = os.environ.get(env_name, "").strip()
            if env_key:
                config.set_api_key(provider_id, env_key)
                _log.info("api key for %s taken from %s", provider_id, env_name)
                break

    # v6.2.0: embedded default key (release builds) fills the OpenRouter
    # slot last — explicit user keys and env vars already won above.
    _apply_default_api(config)

    return config


def save_config(config: AppConfig) -> None:
    """Persist configuration to disk (best-effort, owner-only permissions)."""
    path = config_path()
    try:
        path.write_text(
            json.dumps(config.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        restrict_permissions(path)
    except OSError as exc:
        # Settings still apply for this session; only persistence failed.
        _log.error("could not save config to %s: %s", path, exc)
