"""Default backend: Seed Code's own built-in API connection.

``Default`` is a **first-class provider, deliberately separate from
OpenRouter** in the UI, in configuration, and at request time:

* it owns its own config slot (``providers["default"]``) with its own model;
* it needs **no API key from the user** — :attr:`requires_key` is False, so
  no API-key prompt, no API-key row, and no key validation step ever applies;
* it resolves its credential itself, in this order:

  1. a key stored in Default's OWN slot (advanced/manual use);
  2. the embedded Seed Code release credential (build-time artifact);
  3. ``OPENROUTER_API_KEY`` / ``SEEDCODE_DEFAULT_API_KEY`` in the environment.

  It never reads or writes another provider's slot, and no other provider
  ever reads Default's — so switching between Default and OpenRouter can
  never leak a credential either way.

The Default connection speaks OpenRouter's OpenAI-compatible API as its
*infrastructure* (same base URL, same public model catalogue), which is why
:class:`DefaultProvider` reuses the request plumbing of
:class:`~seedcode.core.providers.openrouter.OpenRouterProvider`. That reuse is
an implementation detail: the user-facing provider, its label, its settings,
its status, and its stored configuration are all its own, and internal
backend names are never shown (see :meth:`_auth_message` and
:meth:`list_models`).

Honesty rules: with no built-in credential available (a source checkout, or a
build without the embedded key) the provider says so plainly and stays
"Setup needed" instead of pretending to be ready; connection status always
comes from a real request, never an assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .. import http as pooled_http  # noqa: F401  (kept for parity with siblings)
from .base import STATUS_NO_KEY, ValidationResult
from .openrouter import (
    MODE_FREE,
    OpenRouterProvider,
    _BASE_URL,
    _CHAT_TIMEOUT,
    _HEADERS,
)

if TYPE_CHECKING:
    from ..models import AppConfig

# Shown when no built-in credential exists in this build.
_NO_CREDENTIAL = (
    "Seed Code's built-in connection is not available in this build. "
    "Choose OpenRouter with /provider and add your own API key, or set "
    "OPENROUTER_API_KEY in the environment."
)


@dataclass
class DefaultProvider(OpenRouterProvider):
    """Seed Code's built-in API connection (no user API key required)."""

    def __post_init__(self) -> None:
        self.id = "default"
        self.label = "Default"
        self.base_url = _BASE_URL
        self.backend_label = "Seed Code API"
        self.requires_key = False
        self.local = False
        self.key_hint = ""

    # --- credential resolution (this provider's slot only) -------------------
    def resolve_key(self, config: "AppConfig") -> tuple[str, str]:
        """``(key, source)`` for the built-in connection.

        Source is ``"stored" | "embedded" | "env" | "none"``. Only Default's
        own config slot is consulted before the built-in credential.
        """
        from ...default_api import resolve_builtin_key

        stored = config.get_api_key(self.id).strip()
        if stored:
            return stored, "stored"
        return resolve_builtin_key()

    def _auth_message(self) -> str:
        """Never tell a Default user to run /apikey (it has no key)."""
        return (
            "Seed Code's built-in credential was rejected. Run /provider and "
            "choose OpenRouter to use your own API key."
        )

    # --- validation / reachability -------------------------------------------
    def validate_key(self, api_key: str) -> ValidationResult:
        """Validate the built-in credential with a real authenticated request."""
        key = (api_key or "").strip()
        if not key:
            return ValidationResult(False, _NO_CREDENTIAL)
        return super().validate_key(key)

    def detect(self, config: "AppConfig") -> bool:
        """True when the built-in connection actually answers right now."""
        key, _ = self.resolve_key(config)
        if not key:
            return False
        return self.validate_key(key).ok

    def refresh_status(self, config: "AppConfig") -> str:
        """Cache an honest status; 'No API Key' when no built-in credential exists."""
        if not self.resolve_key(config)[0]:
            self.status = STATUS_NO_KEY
            return self.status
        return super().refresh_status(config)

    def unavailable_hint(self, config: "AppConfig") -> str:
        if not self.resolve_key(config)[0]:
            return _NO_CREDENTIAL
        return (
            "Seed Code's built-in connection is not reachable right now. "
            "Check your connection, or run /provider to use your own key."
        )

    # --- settings ------------------------------------------------------------
    def extra_settings(self, config: "AppConfig") -> dict[str, str]:
        """No user-facing settings: Default has no key and no mode to pick."""
        return {}

    def set_extra_setting(
        self, config: "AppConfig", name: str, value: str
    ) -> tuple[bool, str]:
        return False, f"{self.label} has no setting '{name}'."

    def mode(self, config: "AppConfig") -> str:
        """Default always lists zero-cost models (its built-in credential is free-tier).

        Kept independent of OpenRouter's own free/pro setting so switching
        one provider never changes what the other lists.
        """
        return MODE_FREE

    # --- request plumbing (the built-in credential, never another slot) -------
    def _get_client(self, config: "AppConfig") -> Any:
        """Cached OpenAI client for the built-in credential (import deferred)."""
        from openai import OpenAI

        api_key, _ = self.resolve_key(config)
        if self._client is None or self._client_key != api_key:
            self._client = OpenAI(
                api_key=api_key,
                base_url=_BASE_URL,
                default_headers=_HEADERS,
                timeout=_CHAT_TIMEOUT,
                max_retries=0,  # the engine owns retry policy
            )
            self._client_key = api_key
        return self._client
