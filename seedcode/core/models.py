"""Pydantic data models for Seed Code.

All persisted and in-memory structured data flows through these models so that
validation happens in one place and the rest of the app can rely on typed data.
"""

from __future__ import annotations

import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..utils.text import strip_surrogates

Role = Literal["system", "user", "assistant", "tool"]


def clean_values(value: Any) -> Any:
    """Recursively neutralise surrogates in strings inside a JSON-ish value."""
    if isinstance(value, str):
        return strip_surrogates(value)
    if isinstance(value, dict):
        return {clean_values(key): clean_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_values(item) for item in value]
    return value

# Safe completion budget sent with chat requests when the user has not
# overridden it. Free-tier accounts are rejected (HTTP 402) when the requested
# budget exceeds what their credits could cover, so the default stays small.
DEFAULT_MAX_TOKENS = 1024

# Agent turns write code across files, so they get a higher ceiling than the
# plain-chat clamp (providers that cannot afford it still cap via the
# ``:free`` rule in :meth:`AppConfig.effective_max_tokens`).
AGENT_MAX_TOKENS = 8192

# The built-in backends, kept as a Literal so a bad *built-in* id fails
# loudly. A user-defined provider id (``custom:<slug>``) is also valid, which
# is why ``AppConfig.active_provider`` is a plain ``str``: the Custom provider
# system (v7.2.5) lets users save any number of their own configurations, and
# a closed Literal could not express them.
ProviderId = Literal[
    "default",
    "openrouter",
    "freemodel_claude",
    "freemodel_codex",
    "aerolink",
    "ollama",
]

#: Prefix that marks a user-defined provider id (``custom:<slug>``).
CUSTOM_PROVIDER_PREFIX = "custom:"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def custom_provider_id(name: str) -> str:
    """Stable id for a user-defined provider, derived from its display name."""
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return f"{CUSTOM_PROVIDER_PREFIX}{slug[:48] or 'provider'}"


def is_custom_provider_id(provider_id: str) -> bool:
    """Whether ``provider_id`` names a user-defined provider."""
    return (provider_id or "").strip().lower().startswith(CUSTOM_PROVIDER_PREFIX)


def valid_base_url(url: str) -> bool:
    """Whether a user-entered base URL is an http(s) endpoint."""
    return (url or "").strip().lower().startswith(("http://", "https://"))


class CustomProviderConfig(BaseModel):
    """A user-defined, OpenAI-compatible API provider (v7.2.5).

    The Custom system lets the user point Seed Code at *any* compatible
    endpoint (a self-hosted gateway, a proxy, another vendor) without that
    vendor being hard-coded into the application. Each saved configuration is
    fully self-contained: its own name, base URL, API key, model and enabled
    flag, ordered by ``priority``. There is no artificial count limit — users
    may save as many as they need, and each is persisted in ``config.json``
    beside the built-in providers.

    Credentials are stored exactly like every other provider's key (owner-only
    file permissions) and are never logged or printed in full; see
    :meth:`AppConfig.masked_key`.
    """

    id: str = ""
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    enabled: bool = True
    #: Lower sorts first. Kept contiguous by the move/remove helpers.
    priority: int = 0

    @field_validator("id", "name", "base_url", "api_key", "model")
    @classmethod
    def _clean(cls, value: str) -> str:
        return (value or "").strip()

    @property
    def label(self) -> str:
        """Display name (falls back to the id if the name is empty)."""
        return self.name or self.id


class ToolCallRecord(BaseModel):
    """One native tool invocation recorded on an assistant message.

    ``id`` is the provider-issued call id ("" for text-protocol calls, which
    have none); providers convert records to their own wire format when
    serialising history.
    """

    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def _clean_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Neutralise surrogates anywhere in tool arguments (v7.1.0).

        A lone surrogate in a model-supplied path or file body would otherwise
        raise on the first ``encode("utf-8")`` — in a file write, an HTTP
        request, or the console.
        """
        return clean_values(value)


class Message(BaseModel):
    """A single chat message in a conversation."""

    role: Role
    content: str
    timestamp: float = Field(default_factory=time.time)
    # Base64 PNG attachments (desktop screenshots). Only providers that
    # support vision read these; ``to_api`` stays text-only so plain
    # backends are never sent a shape they cannot handle.
    images: list[str] = Field(default_factory=list)
    # Native tool calling. ``tool_calls`` is set on assistant messages that
    # invoked tools; ``tool_call_id``/``tool_name`` are set on role=="tool"
    # result messages. All default so old histories load unchanged, and
    # providers that never learned these roles simply never see them (the
    # agent downgrades native-era history before a text-only request).
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    tool_call_id: str = ""
    tool_name: str = ""

    @field_validator("content", "tool_name", "tool_call_id")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        """Repair/replace lone surrogates in every message body (v7.1.0).

        This is the single boundary for model output, tool results and loaded
        history: after this validator, no message can carry text that fails to
        encode as UTF-8. Valid Unicode (Bangla, emoji, CJK, Arabic…) is
        preserved exactly.
        """
        return strip_surrogates(value) if isinstance(value, str) else value

    def to_api(self) -> dict[str, str]:
        """Return the minimal shape chat-completions style APIs expect."""
        return {"role": self.role, "content": self.content}


class ProviderConfig(BaseModel):
    """Per-provider settings: each backend keeps its own key and model.

    Switching providers never touches another provider's entry, so keys and
    model choices are always remembered. Ollama and the built-in ``default``
    provider simply leave ``api_key`` empty (they do not need one).
    ``options`` holds provider-specific extras (e.g. OpenRouter's free/pro
    mode) so new providers can add settings without schema changes.
    """

    api_key: str = ""
    model: str = ""
    options: dict[str, str] = Field(default_factory=dict)


_ALL_PROVIDERS = (
    "default",
    "openrouter",
    "freemodel_claude",
    "freemodel_codex",
    "aerolink",
    "ollama",
)

# Fallback used by legacy migration for a provider that is missing or
# unrecognised. Kept next to _ALL_PROVIDERS so the two can never drift: the
# built-in Default provider needs no key, so it is the safe landing spot.
_FALLBACK_PROVIDER = _ALL_PROVIDERS[0]


def _default_providers() -> dict[str, ProviderConfig]:
    return {pid: ProviderConfig() for pid in _ALL_PROVIDERS}


class AppConfig(BaseModel):
    """Persisted application configuration.

    Stored shape (config.json)::

        active_provider: "default" | "openrouter" | "freemodel_claude"
                         | "freemodel_codex" | "aerolink" | "ollama"
                         | "custom:<slug>"
        providers:
          default:          {api_key(unused), model}
          openrouter:       {api_key, model}
          freemodel_claude: {api_key, model}
          freemodel_codex:  {api_key, model}
          aerolink:         {api_key, model}
          ollama:           {api_key(unused), model}
        custom_providers:
          - {id, name, base_url, api_key, model, enabled, priority}

    Each provider's entry is fully isolated: writing one never touches
    another, so a switch can never leak a key or a model between them.
    User-defined providers (``custom_providers``) are likewise self-contained.

    Models are never hardcoded — each provider's ``model`` starts empty and
    the user selects one from the live catalogue. Older config formats
    (v0.x flat ``api_key``, v1.x ``api_keys``/``models`` maps, v2.x single
    ``freemodel`` entry with claude/codex sub-backends) migrate
    automatically on load.
    """

    active_provider: str = "default"
    providers: dict[str, ProviderConfig] = Field(default_factory=_default_providers)
    #: User-defined OpenAI-compatible providers (unbounded count), v7.2.5.
    custom_providers: list[CustomProviderConfig] = Field(default_factory=list)
    ollama_host: str = "http://localhost:11434"
    theme: str = "seed"
    username: str = "You"
    stream: bool = True
    # Completion-token budget for chat requests. Users may override in
    # config.json; the value is clamped before every request.
    max_tokens: int = DEFAULT_MAX_TOKENS
    # The runtime mode (v8.1.0): exactly chat | code | agent. Chat only
    # converses; Code is the workspace coding agent; Agent is general-purpose
    # execution. There is no fourth mode — the old Assist Mode is Agent Mode
    # (see seedcode.core.modes). ``agent_mode`` stays available as a derived
    # compatibility property rather than a second stored field.
    mode: Literal["chat", "code", "agent"] = "chat"
    # Single hierarchical permission level (see seedcode.tools.permissions):
    # read_only < workspace < desktop < full_system. Desktop automation is a
    # capability of the ``desktop``/``full_system`` levels — there is no
    # separate desktop toggle. ``full_access`` is accepted as a legacy alias
    # for ``full_system`` and migrated on load.
    permission_mode: Literal[
        "read_only", "workspace", "desktop", "full_system"
    ] = "workspace"

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: Any) -> Any:
        """Accept pre-3.x config files and keyword shorthand.

        Handles: v0.x (flat ``api_key`` string, display-name provider),
        v1.x (``provider``/``model`` fields plus ``api_keys``/``models``
        maps), v2.x (single ``freemodel`` provider with claude/codex
        sub-backends), and constructor convenience (``AppConfig(model=...)``).
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)  # never mutate the caller's dict

        # v8.1.0: one canonical mode. A legacy ``agent_mode`` boolean maps onto
        # it (True -> agent), and the obsolete key is dropped so it can never
        # resurface as a competing source of truth. Unknown/legacy mode names
        # (including "assist") resolve through the shared parser, so no fourth
        # mode can be created by a stored value.
        from .modes import Mode, parse_mode

        legacy_agent = data.pop("agent_mode", None)
        data["mode"] = parse_mode(
            data.get("mode"),
            default=Mode.AGENT if legacy_agent else Mode.CHAT,
        ).value

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
        # Sanitised AFTER the legacy-freemodel split below, which may map it.
        raw_active = data.pop("provider", None) or data.get("active_provider")
        if isinstance(raw_active, str):
            data["active_provider"] = norm(raw_active)

        # v1.x per-provider maps.
        for pid, key in (data.pop("api_keys", None) or {}).items():
            providers.setdefault(norm(pid), {})["api_key"] = key
        for pid, model in (data.pop("models", None) or {}).items():
            providers.setdefault(norm(pid), {}).setdefault("model", model)

        # v1.x top-level model belongs to the active provider.
        top_model = data.pop("model", None)
        if top_model:
            active = data.get("active_provider", _FALLBACK_PROVIDER)
            providers.setdefault(active, {})["model"] = top_model

        # v0.x: single "api_key" string belonged to OpenRouter.
        legacy_key = data.pop("api_key", None)
        if legacy_key:
            providers.setdefault("openrouter", {}).setdefault("api_key", legacy_key)

        # v2.x: one "freemodel" provider with claude/codex sub-backends
        # splits into the two first-class providers. The shared key goes to
        # both; each backend's stashed model goes to its own entry.
        legacy_fm = providers.pop("freemodel", None)
        if legacy_fm is not None:
            options = dict(legacy_fm.get("options") or {})
            backend = (options.pop("backend", "") or "codex").strip().lower()
            key = legacy_fm.get("api_key", "")
            models_by_backend = {
                "claude": options.pop("model_claude", ""),
                "codex": options.pop("model_codex", ""),
            }
            if legacy_fm.get("model"):
                models_by_backend[backend if backend in models_by_backend else "codex"] = (
                    legacy_fm["model"]
                )
            for suffix in ("claude", "codex"):
                entry = providers.setdefault(f"freemodel_{suffix}", {})
                entry.setdefault("api_key", key)
                entry.setdefault("model", models_by_backend[suffix])
            if data.get("active_provider") == "freemodel":
                data["active_provider"] = (
                    "freemodel_claude" if backend == "claude" else "freemodel_codex"
                )

        # A user-defined provider id (``custom:<slug>``) is valid as long as it
        # is actually defined in this config; anything else unknown falls back
        # to the built-in Default provider (the zero-setup option).
        custom_ids = {
            norm(str(entry.get("id", "")))
            for entry in (data.get("custom_providers") or [])
            if isinstance(entry, dict)
        }
        if (
            "active_provider" in data
            and data["active_provider"] not in _ALL_PROVIDERS
            and data["active_provider"] not in custom_ids
        ):
            data["active_provider"] = _FALLBACK_PROVIDER

        # vNext: the standalone ``desktop_mode`` flag folded into the unified
        # permission level. A legacy config with desktop_mode=true elevates a
        # workspace level to ``desktop``; ``full_access`` renames to
        # ``full_system``. ``desktop_mode`` is dropped from the model.
        legacy_desktop = data.pop("desktop_mode", None)
        raw_perm = str(data.get("permission_mode", "") or "").strip().lower()
        if raw_perm in ("full_access", "fullaccess", "full", "system"):
            data["permission_mode"] = "full_system"
            raw_perm = "full_system"
        if legacy_desktop and raw_perm in ("", "read_only", "workspace"):
            # Desktop was on: grant at least the desktop level.
            data["permission_mode"] = "desktop"

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

    # --- mode (derived compatibility view over ``mode``) ---------------------
    @property
    def agent_mode(self) -> bool:
        """Whether the model may act on the project (Code or Agent mode).

        Compatibility shim for the pre-8.1 boolean: the mode is the single
        source of truth now and this derives from it, so the two can never
        disagree. Assigning it selects a mode (True -> Agent, False -> Chat).
        """
        return self.mode in ("code", "agent")

    @agent_mode.setter
    def agent_mode(self, value: bool) -> None:
        if value:
            if self.mode == "chat":
                self.mode = "agent"  # type: ignore[assignment]
        else:
            self.mode = "chat"  # type: ignore[assignment]

    # --- desktop capability (derived from the permission level) --------------
    @property
    def desktop_mode(self) -> bool:
        """Whether the current permission level allows desktop automation.

        Compatibility shim: desktop is no longer a separate flag but a
        capability of the ``desktop``/``full_system`` levels. Reads derive from
        ``permission_mode``; assigning True/False raises or lowers the level so
        older call sites keep working.
        """
        from ..tools.permissions import PermissionLevel

        return PermissionLevel.parse(self.permission_mode).allows_desktop

    @desktop_mode.setter
    def desktop_mode(self, value: bool) -> None:
        from ..tools.permissions import PermissionLevel

        current = PermissionLevel.parse(self.permission_mode)
        if value and not current.allows_desktop:
            self.permission_mode = "desktop"  # type: ignore[assignment]
        elif not value and current.allows_desktop:
            # Drop desktop capability but keep the ability to edit the project.
            self.permission_mode = "workspace"  # type: ignore[assignment]

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
        custom = self.custom_provider(self.active_provider)
        if custom is not None:
            return custom.model
        entry = self.providers.get(self.active_provider)
        return entry.model if entry else ""

    @model.setter
    def model(self, value: str) -> None:
        custom = self.custom_provider(self.active_provider)
        if custom is not None:
            custom.model = value
            return
        if self.active_provider not in self.providers:
            self.providers[self.active_provider] = ProviderConfig()
        self.providers[self.active_provider].model = value

    # --- key management -----------------------------------------------------
    def get_api_key(self, provider_id: str | None = None) -> str:
        """Key for ``provider_id`` (default: the active provider)."""
        pid = (provider_id or self.active_provider).lower()
        custom = self.custom_provider(pid)
        if custom is not None:
            return custom.api_key
        entry = self.providers.get(pid)
        return entry.api_key if entry else ""

    def set_api_key(self, provider_id: str, key: str) -> None:
        pid = provider_id.lower()
        custom = self.custom_provider(pid)
        if custom is not None:
            custom.api_key = key
            return
        if pid not in self.providers:
            self.providers[pid] = ProviderConfig()
        self.providers[pid].api_key = key

    # --- user-defined providers (Custom, v7.2.5) ----------------------------
    def custom_provider(
        self, provider_id: str | None = None
    ) -> CustomProviderConfig | None:
        """The saved custom configuration for ``provider_id`` (or None)."""
        pid = (provider_id or self.active_provider).strip().lower()
        for entry in self.custom_providers:
            if entry.id.lower() == pid:
                return entry
        return None

    def ordered_custom_providers(
        self, *, enabled_only: bool = False
    ) -> list[CustomProviderConfig]:
        """Custom providers in priority order (optionally enabled only)."""
        entries = [e for e in self.custom_providers if e.enabled or not enabled_only]
        return sorted(entries, key=lambda e: (e.priority, e.name.lower()))

    def add_custom_provider(
        self,
        name: str,
        base_url: str,
        api_key: str = "",
        model: str = "",
        *,
        enabled: bool = True,
    ) -> CustomProviderConfig:
        """Create and append a user-defined provider (unbounded count).

        Raises ``ValueError`` for an empty name or a non-http(s) base URL so
        callers can show a precise error instead of saving a broken entry.
        The id is derived from the name and made unique.
        """
        name = (name or "").strip()
        base_url = (base_url or "").strip().rstrip("/")
        if not name:
            raise ValueError("a custom provider needs a name")
        if not valid_base_url(base_url):
            raise ValueError("base URL must start with http:// or https://")
        base_id = custom_provider_id(name)
        existing = {e.id.lower() for e in self.custom_providers}
        pid = base_id.lower()
        suffix = 2
        while pid in existing:
            pid = f"{base_id.lower()}-{suffix}"
            suffix += 1
        entry = CustomProviderConfig(
            id=pid,
            name=name,
            base_url=base_url,
            api_key=(api_key or "").strip(),
            model=(model or "").strip(),
            enabled=enabled,
        )
        self.custom_providers.append(entry)
        self._renumber_custom()
        return entry

    def update_custom_provider(
        self,
        provider_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        enabled: bool | None = None,
    ) -> CustomProviderConfig | None:
        """Edit a saved custom provider; returns it, or None if unknown."""
        entry = self.custom_provider(provider_id)
        if entry is None:
            return None
        if name is not None and name.strip():
            entry.name = name.strip()
        if base_url is not None:
            url = base_url.strip().rstrip("/")
            if not valid_base_url(url):
                raise ValueError("base URL must start with http:// or https://")
            entry.base_url = url
        if api_key is not None:
            entry.api_key = api_key.strip()
        if model is not None:
            entry.model = model.strip()
        if enabled is not None:
            entry.enabled = bool(enabled)
        return entry

    def remove_custom_provider(self, provider_id: str) -> bool:
        """Delete a saved custom provider. The active one falls back safely."""
        entry = self.custom_provider(provider_id)
        if entry is None:
            return False
        self.custom_providers.remove(entry)
        self.providers.pop(entry.id.lower(), None)
        if self.active_provider.lower() == entry.id.lower():
            from .. import defaults as _defaults

            self.active_provider = _defaults.DEFAULT_PROVIDER
        self._renumber_custom()
        return True

    def set_custom_enabled(self, provider_id: str, enabled: bool) -> bool:
        """Enable/disable a saved custom provider (disabled = never used)."""
        entry = self.custom_provider(provider_id)
        if entry is None:
            return False
        entry.enabled = bool(enabled)
        return True

    def move_custom_provider(self, provider_id: str, delta: int) -> bool:
        """Move a custom provider up/down in the priority order."""
        entry = self.custom_provider(provider_id)
        if entry is None or delta == 0:
            return False
        order = self.ordered_custom_providers()
        index = order.index(entry)
        target = max(0, min(len(order) - 1, index + delta))
        if target == index:
            return False
        order.insert(target, order.pop(index))
        for position, item in enumerate(order):
            item.priority = position
        # Keep the persisted list in the same order the user now sees.
        self.custom_providers = order
        return True

    def _renumber_custom(self) -> None:
        """Keep priority contiguous after an add/delete."""
        for position, item in enumerate(self.ordered_custom_providers()):
            item.priority = position

    def provider_options(self, provider_id: str) -> dict[str, str]:
        """Mutable provider-specific options dict for ``provider_id``."""
        pid = provider_id.lower()
        if pid not in self.providers:
            self.providers[pid] = ProviderConfig()
        return self.providers[pid].options

    def is_configured(self) -> bool:
        """True when the active provider is usable and a model is chosen.

        Ollama needs no key at all. The built-in ``default`` provider needs no
        USER key either, but its built-in credential must actually exist in
        this build — without one it is not usable, so guided setup still runs
        (the dashboard shows "Setup needed" rather than faking "Ready").
        Every other provider needs its own stored key.
        """
        if not self.model:
            return False
        custom = self.custom_provider(self.active_provider)
        if custom is not None:
            # A custom provider is usable when it is enabled, has a model and
            # carries its own key (never another provider's).
            return bool(custom.enabled and custom.model and custom.api_key.strip())
        if self.active_provider == "ollama":
            return True
        if self.active_provider == "default":
            from ..default_api import builtin_api_available

            return builtin_api_available() or bool(
                self.get_api_key("default").strip()
            )
        return bool(self.get_api_key().strip())

    def remember_model(self) -> None:
        """Compatibility no-op: models are stored per provider already."""

    def recall_model(self) -> str:
        """Model saved for the active provider ('' if none yet)."""
        return self.model

    def effective_max_tokens(self) -> int:
        """Completion budget to send with a request, clamped to a safe range.

        Plain chat clamps to [1, 4096] so an oversized config value can never
        trigger the gateway's 402 "more credits or fewer max_tokens"
        rejection. Agent mode raises the ceiling to ``AGENT_MAX_TOKENS``
        (multi-file code generation needs room). Free models (``*:free``) are
        further capped at ``DEFAULT_MAX_TOKENS`` since their credit ceiling
        is lowest.
        """
        ceiling = AGENT_MAX_TOKENS if self.agent_mode else 4096
        max_tokens = max(1, min(self.max_tokens, ceiling))
        # Agent turns generate whole files; the small chat default would
        # truncate them. A user who explicitly set a different budget keeps it.
        if self.agent_mode and self.max_tokens == DEFAULT_MAX_TOKENS:
            max_tokens = AGENT_MAX_TOKENS
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
