"""Configuration loading and saving for Seed Code.

The config is a single JSON document under ``~/.seedcode/config.json``. Loading
is fault-tolerant: a missing or corrupt file yields sane defaults rather than a
crash, honouring the rule "never crash". Saving is best-effort for the same
reason (a locked or full disk must not kill the session).

Startup resolution order (highest priority first), per provider:

1. explicit user key stored in that provider's OWN config slot (/apikey);
2. environment variable — including values loaded from a project-local ``.env``;
3. the embedded release default (``seedcode/_default_key.py``, if packaged),
   which belongs to the built-in ``default`` provider only and is never
   copied into another provider's slot.

A project-local ``.env`` is the local source of truth for development: it is
read into the process environment (never overriding a real environment
variable) so ``OPENROUTER_API_KEY=...`` works without the user pasting a key.
Secret values are never logged, printed, or written anywhere but the config.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .. import defaults as _defaults
from ..core.models import AppConfig, ProviderConfig
from ..utils.helpers import app_dir, config_path, restrict_permissions
from ..utils.logger import get_logger
from .defaults import DOTENV_DISABLE_ENV, DOTENV_PATH_ENV, ENV_KEYS  # noqa: F401

_log = get_logger("config")

_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Values that mean "dotenv loading is disabled".
_DISABLED = {"1", "true", "yes", "on"}

# Environment overrides for the provider/model selection. They sit BELOW the
# stored user config (an explicit /provider or /model choice always wins) and
# ABOVE the built-in defaults, so a launcher or CI script can preselect a
# setup without silently overriding what the user picked in the app.
_PROVIDER_ENV = "SEEDCODE_PROVIDER"
_MODEL_ENV = "SEEDCODE_MODEL"


# --- project .env ------------------------------------------------------------
def _dotenv_candidates() -> list[Path]:
    """Files to read for local configuration, in order.

    ``SEEDCODE_DOTENV`` (when set) pins a single path; otherwise the current
    working directory's ``.env`` is tried first (the project the user launched
    from), then the per-user app directory's ``.env``.
    """
    override = (os.environ.get(DOTENV_PATH_ENV) or "").strip()
    if override:
        return [Path(override)]
    candidates = [Path.cwd() / ".env"]
    try:
        candidates.append(app_dir() / ".env")
    except OSError:
        pass  # an unwritable home must not break config loading
    return candidates


def parse_dotenv(text: str) -> list[tuple[str, str]]:
    """Parse ``KEY=VALUE`` pairs from dotenv text (comments/blanks ignored).

    Supports an optional ``export`` prefix and single/double-quoted values.
    Invalid names and empty values are skipped — the parser never guesses.
    """
    pairs: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not _ENV_NAME_RE.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            # Strip an inline comment only when introduced by whitespace.
            hash_at = value.find(" #")
            if hash_at != -1:
                value = value[:hash_at].rstrip()
        if value:
            pairs.append((name, value))
    return pairs


def load_dotenv(*, override: bool = False, paths: list[Path] | None = None) -> list[str]:
    """Load ``.env`` files into the process environment.

    Real environment variables always win unless ``override`` is set, so a
    shell-exported key is never clobbered by a stray file. Returns the list of
    variable NAMES that were applied (never their values, which are secrets).
    """
    if (os.environ.get(DOTENV_DISABLE_ENV) or "").strip().lower() in _DISABLED:
        return []

    applied: list[str] = []
    for path in (paths if paths is not None else _dotenv_candidates()):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name, value in parse_dotenv(text):
            if not override and (os.environ.get(name) or "").strip():
                continue  # a real environment value takes precedence
            os.environ[name] = value
            applied.append(name)
        if applied:
            # Log the provenance only — never the values.
            _log.info("loaded %d variable(s) from %s", len(applied), path)
    return applied


def _apply_default_api(config: AppConfig) -> None:
    """Report the built-in connection's availability — touching no key slot.

    Earlier releases copied the embedded release credential into the
    **OpenRouter** slot, which meant choosing OpenRouter silently inherited
    Seed Code's built-in key. v6.2.5 stops that: the built-in credential
    belongs to the ``default`` provider, which resolves it per request
    (own slot → embedded → environment, see
    :mod:`seedcode.core.providers.default`). Nothing is written to any
    provider's stored configuration here, so the providers stay isolated.
    """
    from ..default_api import builtin_key_source

    source = builtin_key_source()
    if source != "none":
        _log.info("built-in default connection available (%s)", source)


def apply_default_selection(config: AppConfig) -> None:
    """Seed the default provider and model onto a freshly created config.

    The shipped default is the built-in ``default`` provider, which needs no
    API key — so a fresh release install can chat immediately. Applied only
    on a first run (no config file) or when the file is unreadable, so an
    existing configuration is never overwritten. Users can change both any
    time with /provider and /model.

    ``SEEDCODE_PROVIDER`` / ``SEEDCODE_MODEL`` environment variables (also
    readable from a project ``.env``) override the built-in defaults for the
    same first-run case only — a stored user selection is never replaced.
    Unknown provider ids are ignored (the built-in default stands).
    """
    provider_id = (
        (os.environ.get(_PROVIDER_ENV) or "").strip().lower()
        or _defaults.DEFAULT_PROVIDER
    )
    if provider_id not in _KNOWN_PROVIDERS():
        _log.warning("%s '%s' is not a known provider; using the default", _PROVIDER_ENV, provider_id)
        provider_id = _defaults.DEFAULT_PROVIDER

    model = (
        (os.environ.get(_MODEL_ENV) or "").strip()
        or _defaults.DEFAULT_MODEL
    )

    config.active_provider = provider_id  # type: ignore[assignment]
    entry = config.providers.get(provider_id)
    if entry is None:
        config.providers[provider_id] = ProviderConfig(model=model)
    elif not entry.model:
        entry.model = model


def _KNOWN_PROVIDERS() -> frozenset[str]:
    """The registered provider ids (late import to avoid a cycle)."""
    from ..core.providers import PROVIDERS

    return frozenset(PROVIDERS)


def load_config() -> AppConfig:
    """Load configuration from disk, falling back to defaults on any error."""
    # A project-local .env is read first so its keys participate in the
    # environment-variable tier below (real env vars still win).
    load_dotenv()

    path = config_path()
    fresh = False
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
            fresh = True
    else:
        _log.info("no config file yet (first run)")
        fresh = True

    if fresh:
        apply_default_selection(config)

    # Explicit environment variables always win over stored API keys. Each
    # variable writes only the provider it names — never a shared slot.

    # Explicit environment variables always win over stored API keys.
    for provider_id, env_names in ENV_KEYS.items():
        for env_name in env_names:
            env_key = os.environ.get(env_name, "").strip()
            if env_key:
                config.set_api_key(provider_id, env_key)
                _log.info("api key for %s taken from %s", provider_id, env_name)
                break

    # The built-in Default connection is resolved per request by its own
    # provider; report it last without writing any provider's key slot.
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
