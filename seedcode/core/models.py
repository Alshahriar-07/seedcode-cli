"""Pydantic data models for Seed Code.

All persisted and in-memory structured data flows through these models so that
validation happens in one place and the rest of the app can rely on typed data.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Role = Literal["system", "user", "assistant"]

# Safe completion budget sent with chat requests when the user has not
# overridden it. Free-tier accounts are rejected (HTTP 402) when the requested
# budget exceeds what their credits could cover, so the default stays small.
DEFAULT_MAX_TOKENS = 1024

# The four supported backends (kept as a Literal so bad config fails loudly).
ProviderId = Literal["openrouter", "freemodel", "aerolink", "ollama"]


class Message(BaseModel):
    """A single chat message in a conversation."""

    role: Role
    content: str
    timestamp: float = Field(default_factory=time.time)

    def to_api(self) -> dict[str, str]:
        """Return the minimal shape chat-completions style APIs expect."""
        return {"role": self.role, "content": self.content}


class ProviderConfig(BaseModel):
    """Per-provider settings: each backend keeps its own key and model.

    Switching providers never touches another provider's entry, so keys and
    model choices are always remembered. Ollama simply leaves ``api_key``
    empty (it does not use one). ``options`` holds provider-specific extras
    (e.g. OpenRouter's free/pro mode, FreeModel's claude/codex backend) so
    new providers can add settings without schema changes.
    """

    api_key: str = ""
    model: str = ""
    options: dict[str, str] = Field(default_factory=dict)


_ALL_PROVIDERS = ("openrouter", "freemodel", "aerolink", "ollama")


def _default_providers() -> dict[str, ProviderConfig]:
    return {pid: ProviderConfig() for pid in _ALL_PROVIDERS}


class AppConfig(BaseModel):
    """Persisted application configuration.

    Stored shape (config.json)::

        active_provider: "openrouter" | "freemodel" | "aerolink" | "ollama"
        providers:
          openrouter: {api_key, model}
          freemodel:  {api_key, model}
          aerolink:   {api_key, model}
          ollama:     {api_key(unused), model}

    Models are never hardcoded — each provider's ``model`` starts empty and
    the user selects one from the live catalogue. Older config formats
    (v0.x flat ``api_key``, v1.x ``api_keys``/``models`` maps) migrate
    automatically on load.
    """

    active_provider: ProviderId = "freemodel"
    providers: dict[str, ProviderConfig] = Field(default_factory=_default_providers)
    ollama_host: str = "http://localhost:11434"
    theme: str = "seed"
    username: str = "You"
    stream: bool = True
    # Completion-token budget for chat requests. Users may override in
    # config.json; the value is clamped before every request.
    max_tokens: int = DEFAULT_MAX_TOKENS
    # Agent mode: when on, the model may act on the project through the tool
    # engine. permission_mode bounds what it may touch (see seedcode.tools).
    agent_mode: bool = False
    permission_mode: Literal["read_only", "workspace", "full_access"] = "workspace"

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: Any) -> Any:
        """Accept pre-2.x config files and keyword shorthand.

        Handles: v0.x (flat ``api_key`` string, display-name provider),
        v1.x (``provider``/``model`` fields plus ``api_keys``/``models``
        maps), and constructor convenience (``AppConfig(model=...)``).
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)  # never mutate the caller's dict

        def norm(pid: str) -> str:
            """Provider id normalisation (OpenRouter is a first-class backend)."""
            return pid.strip().lower()

        # Normalise nested provider entries to plain dicts we can merge into.
        providers: dict[str, dict] = {}
        for pid, entry in (data.get("providers") or {}).items():
            if isinstance(entry, ProviderConfig):
                providers[norm(pid)] = entry.model_dump()
            elif isinstance(entry, dict):
                providers[norm(pid)] = dict(entry)

        # Active provider: new field, or legacy "provider" (any casing).
        raw_active = data.pop("provider", None) or data.get("active_provider")
        if isinstance(raw_active, str):
            active = norm(raw_active)
            if active not in _ALL_PROVIDERS:
                active = "freemodel"
            data["active_provider"] = active

        # v1.x per-provider maps.
        for pid, key in (data.pop("api_keys", None) or {}).items():
            providers.setdefault(norm(pid), {})["api_key"] = key
        for pid, model in (data.pop("models", None) or {}).items():
            providers.setdefault(norm(pid), {}).setdefault("model", model)

        # v1.x top-level model belongs to the active provider.
        top_model = data.pop("model", None)
        if top_model:
            active = data.get("active_provider", "freemodel")
            providers.setdefault(active, {})["model"] = top_model

        # v0.x: single "api_key" string belonged to OpenRouter.
        legacy_key = data.pop("api_key", None)
        if legacy_key:
            providers.setdefault("openrouter", {}).setdefault("api_key", legacy_key)

        if providers or "providers" in data:
            data["providers"] = providers
        return data

    @model_validator(mode="after")
    def _ensure_all_providers(self) -> "AppConfig":
        """Every supported provider always has an entry."""
        for pid in _ALL_PROVIDERS:
            if pid not in self.providers:
                self.providers[pid] = ProviderConfig()
        return self

    # --- active provider/model (compatibility + convenience) ----------------
    @property
    def provider(self) -> str:
        """Id of the active provider."""
        return self.active_provider

    @provider.setter
    def provider(self, value: str) -> None:
        self.active_provider = value  # type: ignore[assignment]

    @property
    def model(self) -> str:
        """Model selected for the ACTIVE provider ('' if none yet)."""
        return self.providers[self.active_provider].model

    @model.setter
    def model(self, value: str) -> None:
        self.providers[self.active_provider].model = value

    # --- key management -----------------------------------------------------
    def get_api_key(self, provider_id: str | None = None) -> str:
        """Key for ``provider_id`` (default: the active provider)."""
        pid = (provider_id or self.active_provider).lower()
        entry = self.providers.get(pid)
        return entry.api_key if entry else ""

    def set_api_key(self, provider_id: str, key: str) -> None:
        pid = provider_id.lower()
        if pid not in self.providers:
            self.providers[pid] = ProviderConfig()
        self.providers[pid].api_key = key

    def provider_options(self, provider_id: str) -> dict[str, str]:
        """Mutable provider-specific options dict for ``provider_id``."""
        pid = provider_id.lower()
        if pid not in self.providers:
            self.providers[pid] = ProviderConfig()
        return self.providers[pid].options

    def is_configured(self) -> bool:
        """True when the active provider is usable and a model is chosen.

        Ollama needs no key; the other providers need one.
        """
        if not self.model:
            return False
        if self.active_provider == "ollama":
            return True
        return bool(self.get_api_key().strip())

    def remember_model(self) -> None:
        """Compatibility no-op: models are stored per provider already."""

    def recall_model(self) -> str:
        """Model saved for the active provider ('' if none yet)."""
        return self.model

    def effective_max_tokens(self) -> int:
        """Completion budget to send with a request, clamped to a safe range.

        Values outside [1, 4096] are clamped so an oversized config value can
        never trigger the gateway's 402 "more credits or fewer max_tokens"
        rejection. Free models (``*:free``) are further capped at
        ``DEFAULT_MAX_TOKENS`` since their credit ceiling is lowest.
        """
        max_tokens = max(1, min(self.max_tokens, 4096))
        if self.model.endswith(":free"):
            max_tokens = min(max_tokens, DEFAULT_MAX_TOKENS)
        return max_tokens

    def masked_key(self, provider_id: str | None = None) -> str:
        """Return the API key with the middle obscured for safe display."""
        key = self.get_api_key(provider_id).strip()
        if not key:
            return "(not set)"
        if len(key) <= 12:
            return "*" * len(key)
        return f"{key[:8]}...{key[-4:]}"
