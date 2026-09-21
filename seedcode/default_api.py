"""Built-in API configuration layer.

Two separate credentials are resolved here, and they are never mixed:

**OpenRouter** (the user's own provider):

1. Explicit user key (stored config, entered via /apikey or onboarding)
2. Environment variable (``OPENROUTER_API_KEY``)
3. Embedded Seed Code default key (generated at build time; release
   artifacts only — never committed to source control)

**Default** (Seed Code's built-in connection, a first-class provider of its
own — see :mod:`seedcode.core.providers.default`): the embedded release
credential, else ``OPENROUTER_API_KEY`` / ``SEEDCODE_DEFAULT_API_KEY``. The
v6.2.5 release no longer copies that credential into OpenRouter's stored slot:
Default resolves it per request, so the two providers' configurations stay
isolated.

The embedded key lives in ``seedcode/_default_key.py``, a git-ignored file
generated from the build machine's ``.env`` by
``scripts/windows/embed_default_key.py`` and packaged into the frozen
executable with PyInstaller. Source checkouts simply lack the file and
behave exactly as before (no key, guided setup).

Nothing here ever logs, prints, or echoes the key itself.

Later migration note: this temporary architecture is scheduled to be
replaced by a server-side gateway; the resolution order and this accessor
are the only integration points, so that swap is local.
"""

from __future__ import annotations

import os
from functools import lru_cache

# --- embedded default (build-time artifact; absent in dev checkouts) ---------
_DEFAULT_KEY_MODULE = None


@lru_cache(maxsize=1)
def _load_embedded() -> tuple[str, bool, str]:
    """Import the generated ``_default_key`` module exactly once.

    Returns ``(key, enabled, note)``. Import failure is normal in source
    checkouts (the file is not committed) and yields an empty key.
    """
    try:
        from . import _default_key as mod  # type: ignore[attr-defined]
    except Exception:
        return "", False, "no embedded default key"
    key = (getattr(mod, "DEFAULT_OPENROUTER_API_KEY", "") or "").strip()
    enabled = bool(getattr(mod, "DEFAULT_API_ENABLED", False)) and bool(key)
    note = str(getattr(mod, "DEFAULT_API_NOTE", ""))
    return key, enabled, note


def embedded_default_key() -> str:
    """The embedded built-in Default credential ('' when not packaged)."""
    key, _, _ = _load_embedded()
    return key


def default_api_available() -> bool:
    """True when the release artifact carries the embedded default key."""
    _, enabled, _ = _load_embedded()
    return enabled


def default_api_note() -> str:
    """Human-readable provenance note (never includes the key)."""
    _, _, note = _load_embedded()
    return note


def resolve_openrouter_key(user_key: str = "", env_key: str = "") -> tuple[str, str]:
    """Apply the v6.2.0 credential priority for OpenRouter.

    ``user_key`` is the stored/explicit key and ``env_key`` the
    ``OPENROUTER_API_KEY`` environment value (both optional so callers can
    resolve either combination, e.g. config-load time vs key-removal time).

    Returns ``(key, source)`` where source is ``"user" | "env" | "embedded"``
    (source ``"embedded"`` only when neither explicit value is set; an empty
    key means nothing is available and guided setup should run).
    """
    if user_key.strip():
        return user_key, "user"
    if env_key.strip():
        return env_key, "env"
    embedded, enabled, _ = _load_embedded()
    if enabled:
        return embedded, "embedded"
    return "", "none"


def env_openrouter_key() -> str:
    """The OPENROUTER_API_KEY environment variable ('' when unset)."""
    return (os.environ.get("OPENROUTER_API_KEY", "") or "").strip()


# Environment variables that can supply the built-in connection's credential
# when no release artifact is packaged (source checkouts, CI, self-hosted
# builds). ``SEEDCODE_DEFAULT_API_KEY`` exists so a build can be pointed at a
# different built-in credential without touching the OpenRouter variable.
BUILTIN_KEY_ENVS: tuple[str, ...] = ("OPENROUTER_API_KEY", "SEEDCODE_DEFAULT_API_KEY")


def builtin_key_source() -> str:
    """Where the built-in credential comes from (never the key itself)."""
    return resolve_builtin_key()[1]


def resolve_builtin_key() -> tuple[str, str]:
    """Credential for the built-in ``Default`` provider: embedded, then env.

    This is the ONLY credential the Default provider may use. It deliberately
    does not read another provider's stored config slot, so choosing
    OpenRouter can never silently borrow the built-in credential (and the
    default can never leak into OpenRouter's stored key) — the two providers
    stay logically isolated (see :mod:`seedcode.core.providers.default`).

    Returns ``(key, source)`` with source ``"embedded" | "env" | "none"``.
    """
    embedded, enabled, _ = _load_embedded()
    if enabled:
        return embedded, "embedded"
    for name in BUILTIN_KEY_ENVS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value, "env"
    return "", "none"


def builtin_api_available() -> bool:
    """True when the built-in Default connection has a usable credential."""
    return bool(resolve_builtin_key()[0])


def is_default_only(source: str) -> bool:
    """True when a resolved key came from the embedded default."""
    return source == "embedded"


__all__ = [
    "BUILTIN_KEY_ENVS",
    "builtin_api_available",
    "builtin_key_source",
    "embedded_default_key",
    "default_api_available",
    "default_api_note",
    "resolve_builtin_key",
    "resolve_openrouter_key",
    "env_openrouter_key",
    "is_default_only",
]
