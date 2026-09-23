"""Custom backend: a user-defined OpenAI-compatible API provider (v7.2.5).

The Custom system lets a user configure **any** compatible endpoint — an
OpenAI-compatible gateway, a self-hosted proxy, a vendor not built into Seed
Code — without that vendor being hard-coded into the application. A custom
provider is described by a :class:`~seedcode.core.models.CustomProviderConfig`
(name, base URL, API key, model, enabled, priority) and speaks the standard
``/models`` + ``/chat/completions`` surface.

It reuses the request plumbing of
:class:`~seedcode.core.providers.openrouter.OpenRouterProvider` (same wire
protocol) but owns its own base URL, key slot, catalogue and status, and never
falls back to another provider's configuration. Credentials are used only in
the ``Authorization`` header and are never logged or shown in full.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from .. import http as pooled_http
from .base import ModelInfo, ProviderError, ValidationResult
from .openrouter import _CHAT_TIMEOUT, _TIMEOUT, OpenRouterProvider

if TYPE_CHECKING:
    from ..models import AppConfig, CustomProviderConfig


@dataclass
class CustomProvider(OpenRouterProvider):
    """An OpenAI-compatible provider defined by a saved user configuration."""

    #: The saved configuration's id (``custom:<slug>``).
    config_id: str = field(init=False, default="")
    #: Display name from the configuration (used as the provider label).
    display_name: str = field(init=False, default="")

    def __post_init__(self) -> None:
        # Identity only; a real instance is built via :meth:`from_config`.
        self.id = "custom:provider"
        self.label = "Custom"
        self.base_url = ""
        self.backend_label = "Custom API"
        self.requires_key = True
        self.local = False
        self.key_hint = "the API key issued by this provider"

    @classmethod
    def from_config(cls, cfg: "CustomProviderConfig") -> "CustomProvider":
        """Build a provider bound to ``cfg``."""
        provider = cls()
        provider.configure(cfg)
        return provider

    def configure(self, cfg: "CustomProviderConfig") -> None:
        """(Re)bind this provider to a saved configuration."""
        self.config_id = cfg.id
        self.display_name = cfg.name or cfg.id
        self.id = cfg.id.lower()
        self.label = self.display_name
        self.base_url = (cfg.base_url or "").rstrip("/")
        self._client = None
        self._client_key = ""

    # --- helpers -------------------------------------------------------------
    def _auth_headers(self, config: "AppConfig") -> dict[str, str]:
        """Authorization header for this provider's own key (never another's)."""
        key = config.get_api_key(self.id).strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _auth_message(self) -> str:
        return (
            f"Authentication failed. The {self.label} API key may be invalid — "
            "edit it from /provider."
        )

    # --- validation / catalogue ----------------------------------------------
    def validate_key(self, api_key: str) -> ValidationResult:
        """Validate with a real ``GET /models`` request — never a heuristic."""
        if not self.base_url:
            return ValidationResult(False, f"{self.label} has no base URL configured.")
        key = (api_key or "").strip()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            response = pooled_http.get(
                f"{self.base_url}/models", headers=headers, timeout=_TIMEOUT
            )
        except httpx.TimeoutException:
            return ValidationResult(
                False, f"Timed out reaching {self.label}. Check the base URL."
            )
        except httpx.HTTPError:
            return ValidationResult(
                False, f"Could not reach {self.label} at {self.base_url}."
            )
        if response.status_code < 400:
            return ValidationResult(True, f"{self.label} connection verified.")
        if response.status_code in (401, 403):
            return ValidationResult(
                False,
                f"{self.label} rejected the API key (HTTP {response.status_code}).",
            )
        return ValidationResult(
            False, f"{self.label} responded with HTTP {response.status_code}."
        )

    def list_models(self, config: "AppConfig") -> list[ModelInfo]:
        """The endpoint's live ``/models`` catalogue."""
        try:
            response = pooled_http.get(
                f"{self.base_url}/models",
                headers=self._auth_headers(config),
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json().get("data", [])
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"Timed out fetching the {self.label} model list.", transient=True
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(
                f"Could not fetch the {self.label} model list. Check your connection.",
                transient=True,
            ) from exc
        models = [
            ModelInfo(id=entry["id"], label=entry.get("name") or entry["id"])
            for entry in data
            if entry.get("id")
        ]
        if not models:
            raise ProviderError(f"{self.label} reports no models.")
        models.sort(key=lambda m: m.id)
        return models

    def extra_settings(self, config: "AppConfig") -> dict[str, str]:
        return {"base_url": self.base_url or "(not set)"}

    # --- request plumbing (this provider's base URL + key only) --------------
    def _get_client(self, config: "AppConfig") -> Any:
        from openai import OpenAI

        api_key = config.get_api_key(self.id).strip() or "seedcode-custom"
        cache_key = f"{api_key}|{self.base_url}"
        if self._client is None or self._client_key != cache_key:
            self._client = OpenAI(
                api_key=api_key,
                base_url=self.base_url,
                timeout=_CHAT_TIMEOUT,
                max_retries=0,  # the engine owns retry policy
            )
            self._client_key = cache_key
        return self._client


__all__ = ["CustomProvider"]
