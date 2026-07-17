"""FreeModel backend: free AI models via the FreeModel API.

FreeModel (https://freemodel.dev) offers TWO backends behind one provider,
selected with the provider setting ``backend`` and sharing the FreeModel
API key (``fe_oa_...`` from https://freemodel.dev/dashboard):

* **codex** (default) — OpenAI/Responses-compatible API at
  ``https://api.freemodel.dev``.
* **claude** — Claude-compatible (Anthropic Messages) API at
  ``https://cc.freemodel.dev``.

Each backend keeps its OWN selected model and base URL, and connection
status always reflects the active backend. The catalogue is fetched live —
nothing is hardcoded. Keys are validated ONLY by a real authenticated
request to the active backend — never by format heuristics.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from ..models import DEFAULT_MAX_TOKENS
from ..streaming import iter_stream
from ...utils.logger import get_logger
from .base import ModelInfo, Provider, ProviderError, STATUS_UNKNOWN, ValidationResult

if TYPE_CHECKING:
    from ..models import AppConfig, Message

_log = get_logger("freemodel")

BACKEND_CODEX = "codex"
BACKEND_CLAUDE = "claude"
_BACKENDS = (BACKEND_CODEX, BACKEND_CLAUDE)

# Codex backend: OpenAI-compatible API.
_CODEX_BASE = "https://api.freemodel.dev"
_CODEX_API = f"{_CODEX_BASE}/v1"
_CODEX_MODELS_URL = f"{_CODEX_API}/models"

# Claude backend: Anthropic-Messages-compatible API.
_CLAUDE_BASE = "https://cc.freemodel.dev"
_CLAUDE_MODELS_URL = f"{_CLAUDE_BASE}/v1/models"
_CLAUDE_MESSAGES_URL = f"{_CLAUDE_BASE}/v1/messages"
_CLAUDE_API_VERSION = "2023-06-01"

_TIMEOUT = 20.0
# Chat: fail fast on connect, but give busy free models time to answer.
_CHAT_TIMEOUT = httpx.Timeout(20.0, read=180.0)

# Sentinel model id for Auto mode (resolved per request, never sent as-is).
AUTO_MODEL = "auto"

# Live-catalogue cache for Auto resolution, PER BACKEND: backend -> (at, entries).
_CATALOGUE_TTL_S = 300.0
_catalogue: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _codex_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _claude_headers(api_key: str) -> dict[str, str]:
    headers = {"anthropic-version": _CLAUDE_API_VERSION, "content-type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _fetch_entries(backend: str, api_key: str = "") -> list[dict[str, Any]]:
    """Fetch the live catalogue of one FreeModel backend. Raises ProviderError."""
    if backend == BACKEND_CLAUDE:
        url, headers = _CLAUDE_MODELS_URL, _claude_headers(api_key)
    else:
        url, headers = _CODEX_MODELS_URL, _codex_headers(api_key)
    try:
        response = httpx.get(url, headers=headers, timeout=_TIMEOUT)
        response.raise_for_status()
        data = response.json().get("data", [])
    except httpx.TimeoutException as exc:
        raise ProviderError(
            f"Timed out fetching the FreeModel ({backend}) catalogue.", transient=True
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(
            f"Could not fetch the FreeModel ({backend}) catalogue. Check your connection.",
            transient=True,
        ) from exc
    entries = [e for e in data if e.get("id")]
    if not entries:
        raise ProviderError(f"FreeModel ({backend}) reports no models right now.")
    return entries


def _entries_cached(backend: str, api_key: str = "") -> list[dict[str, Any]]:
    """Catalogue with a short per-backend cache for Auto mode."""
    now = time.monotonic()
    cached = _catalogue.get(backend)
    if cached is not None and now - cached[0] < _CATALOGUE_TTL_S:
        return cached[1]
    entries = _fetch_entries(backend, api_key)
    _catalogue[backend] = (now, entries)
    return entries


def _resolve_auto(backend: str, api_key: str = "") -> str:
    """Pick the best model from the active backend's live list."""
    entries = _entries_cached(backend, api_key)
    best = max(entries, key=lambda e: e.get("context_length") or 0)
    _log.info("auto mode (%s) resolved to %s", backend, best["id"])
    return best["id"]


@dataclass
class FreeModelProvider(Provider):
    # Private per-provider client cache for the codex backend; never shared.
    _client: Any = field(init=False, default=None, repr=False)
    _client_key: str = field(init=False, default="", repr=False)
    # validate_key has a fixed signature; prepare()/backend() remember the
    # configured backend here so validation probes the right endpoint.
    _last_backend: str = field(init=False, default=BACKEND_CODEX, repr=False)

    def __post_init__(self) -> None:
        self.id = "freemodel"
        self.label = "FreeModel"
        self.base_url = _CODEX_BASE  # updated to the active backend on use
        self.requires_key = True
        self.key_hint = "fe_oa_...  (get a free API key: https://freemodel.dev/dashboard)"

    # --- backend selection ----------------------------------------------------
    def backend(self, config: "AppConfig") -> str:
        """Active backend: 'codex' (default) or 'claude'."""
        raw = (config.provider_options("freemodel").get("backend") or BACKEND_CODEX).lower()
        backend = raw if raw in _BACKENDS else BACKEND_CODEX
        self.base_url = _CLAUDE_BASE if backend == BACKEND_CLAUDE else _CODEX_BASE
        self._last_backend = backend
        return backend

    def prepare(self, config: "AppConfig") -> None:
        """Bind validation and status to the configured backend."""
        self.backend(config)

    def extra_settings(self, config: "AppConfig") -> dict[str, str]:
        backend = self.backend(config)
        return {"backend": f"{backend}  (codex = {_CODEX_BASE}, claude = {_CLAUDE_BASE})"}

    def set_extra_setting(
        self, config: "AppConfig", name: str, value: str
    ) -> tuple[bool, str]:
        if name != "backend":
            return False, f"{self.label} has no setting '{name}'."
        new = value.strip().lower()
        if new not in _BACKENDS:
            return False, "backend expects 'codex' or 'claude'."
        options = config.provider_options("freemodel")
        old = self.backend(config)
        if new == old:
            return True, f"FreeModel backend is already {new}."
        # Each backend remembers its own model: stash the old backend's
        # choice, restore the new backend's.
        options[f"model_{old}"] = config.providers["freemodel"].model
        options["backend"] = new
        config.providers["freemodel"].model = options.get(f"model_{new}", "")
        self.base_url = _CLAUDE_BASE if new == BACKEND_CLAUDE else _CODEX_BASE
        self.status = STATUS_UNKNOWN  # status belongs to the active backend
        restored = config.providers["freemodel"].model or "(none — run /model)"
        return True, f"FreeModel backend set to {new}. Model: {restored}"

    # --- key validation (real request against the ACTIVE backend) --------------
    def validate_key(self, api_key: str) -> ValidationResult:
        """Validate with a real authenticated request — no heuristics."""
        key = api_key.strip()
        if not key:
            return ValidationResult(False, "API key is empty.")
        backend = self._last_backend
        if backend == BACKEND_CLAUDE:
            url, headers = _CLAUDE_MODELS_URL, _claude_headers(key)
        else:
            url, headers = _CODEX_MODELS_URL, _codex_headers(key)
        try:
            response = httpx.get(url, headers=headers, timeout=_TIMEOUT)
        except httpx.TimeoutException:
            return ValidationResult(False, "Validation timed out. Check your connection.")
        except httpx.HTTPError:
            return ValidationResult(False, "Could not reach FreeModel. Check your connection.")
        if response.status_code == 200:
            return ValidationResult(True, f"API key verified ({backend} backend).")
        if response.status_code in (401, 403):
            return ValidationResult(False, "API key was rejected by FreeModel.")
        return ValidationResult(
            False, f"Unexpected response from FreeModel (HTTP {response.status_code})."
        )

    def refresh_status(self, config: "AppConfig") -> str:
        self.backend(config)  # bind status to the active backend
        return super().refresh_status(config)

    # --- catalogue --------------------------------------------------------------
    def list_models(self, config: "AppConfig") -> list[ModelInfo]:
        backend = self.backend(config)
        entries = _fetch_entries(backend, config.get_api_key("freemodel"))
        models = []
        for entry in entries:
            ctx = entry.get("context_length")
            models.append(
                ModelInfo(
                    id=entry["id"],
                    label=entry.get("display_name") or entry.get("name") or entry["id"],
                    detail=f"{backend} · {ctx} ctx" if ctx else backend,
                    is_free=True,
                )
            )
        models.sort(key=lambda m: m.id)
        return models

    # --- chat -------------------------------------------------------------------
    def stream_chat(self, config: "AppConfig", messages: list["Message"]) -> Iterator[str]:
        backend = self.backend(config)
        api_key = config.get_api_key("freemodel")
        model = config.model
        if model == AUTO_MODEL:
            model = _resolve_auto(backend, api_key)
        max_tokens = min(config.effective_max_tokens(), DEFAULT_MAX_TOKENS)
        _log.debug("chat request: backend=%s model=%s max_tokens=%d", backend, model, max_tokens)
        if backend == BACKEND_CLAUDE:
            yield from self._stream_claude(api_key, model, max_tokens, messages)
        else:
            yield from self._stream_codex(api_key, model, max_tokens, messages)

    def _stream_codex(
        self, api_key: str, model: str, max_tokens: int, messages: list["Message"]
    ) -> Iterator[str]:
        """OpenAI-compatible streaming against api.freemodel.dev."""
        # The OpenAI SDK is the heaviest import in the app; loading it here
        # (first message) instead of at startup keeps launch fast.
        from openai import (
            APIConnectionError,
            APIError,
            APITimeoutError,
            AuthenticationError,
            OpenAI,
            RateLimitError,
        )

        if self._client is None or self._client_key != api_key:
            self._client = OpenAI(
                api_key=api_key,
                base_url=_CODEX_API,
                timeout=_CHAT_TIMEOUT,
                # The engine owns retry policy; keep the SDK from stacking its own.
                max_retries=0,
            )
            self._client_key = api_key
        try:
            stream = self._client.chat.completions.create(
                model=model,
                messages=[m.to_api() for m in messages],  # type: ignore[arg-type]
                max_tokens=max_tokens,
                stream=True,
            )
            yield from iter_stream(stream)
        except AuthenticationError as exc:
            raise ProviderError(
                "Authentication failed. Your FreeModel key may be invalid — run /apikey."
            ) from exc
        except RateLimitError as exc:
            raise ProviderError(
                "Rate limited by FreeModel. Please wait and try again.", transient=True
            ) from exc
        except APITimeoutError as exc:
            raise ProviderError(
                "The FreeModel request timed out. Please try again.", transient=True
            ) from exc
        except APIConnectionError as exc:
            # DNS failures, SSL errors, and dropped connections all land here.
            raise ProviderError(
                "Network error reaching FreeModel. Check your connection.", transient=True
            ) from exc
        except APIError as exc:
            raise _friendly_api_error(exc, model) from exc

    def _stream_claude(
        self, api_key: str, model: str, max_tokens: int, messages: list["Message"]
    ) -> Iterator[str]:
        """Claude-compatible (Messages API) streaming against cc.freemodel.dev."""
        system = next((m.content for m in messages if m.role == "system"), None)
        turns = [m.to_api() for m in messages if m.role != "system"]
        payload: dict[str, object] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": turns,
            "stream": True,
        }
        if system:
            payload["system"] = system
        try:
            with httpx.stream(
                "POST",
                _CLAUDE_MESSAGES_URL,
                headers=_claude_headers(api_key),
                json=payload,
                timeout=_CHAT_TIMEOUT,
            ) as response:
                if response.status_code in (401, 403):
                    raise ProviderError(
                        "Authentication failed. Your FreeModel key may be invalid — run /apikey."
                    )
                if response.status_code == 404:
                    raise ProviderError(
                        f"Model '{model}' was not found on FreeModel. Pick another with /model."
                    )
                if response.status_code == 408:
                    raise ProviderError(
                        "FreeModel timed out handling the request. Please try again.",
                        transient=True,
                    )
                if response.status_code == 429:
                    raise ProviderError(
                        "Rate limited by FreeModel. Please wait and try again.",
                        transient=True,
                    )
                if response.status_code >= 500:
                    raise ProviderError(
                        f"FreeModel had a server error (HTTP {response.status_code}). "
                        "Please try again.",
                        transient=True,
                    )
                if response.status_code >= 400:
                    detail = response.read().decode("utf-8", "replace")[:300]
                    raise ProviderError(
                        f"FreeModel error (HTTP {response.status_code}): {detail}"
                    )
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except ValueError:
                        continue  # tolerate keep-alive noise
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield delta["text"]
                    elif event.get("type") == "error":
                        message = (event.get("error") or {}).get(
                            "message", "Unknown FreeModel error."
                        )
                        raise ProviderError(f"FreeModel error: {message}")
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "The FreeModel request timed out. Please try again.", transient=True
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Network error reaching FreeModel. Check your connection.", transient=True
            ) from exc


def _friendly_api_error(exc: Any, model: str) -> ProviderError:
    """Translate FreeModel API errors into actionable user messages."""
    status = getattr(exc, "status_code", None)
    detail = getattr(exc, "message", str(exc)) or "Unknown API error."
    if status == 402:
        return ProviderError(
            "FreeModel rejected the request (HTTP 402). Pick another model with "
            "/model, or lower max_tokens in /settings."
        )
    if status == 403:
        return ProviderError(
            "FreeModel refused the request (HTTP 403). Your key may lack access "
            "to this model — pick another with /model."
        )
    if status == 404:
        return ProviderError(
            f"Model '{model}' was not found on FreeModel. Pick another with /model."
        )
    if status == 408:
        return ProviderError(
            "FreeModel timed out handling the request. Please try again.", transient=True
        )
    if status is not None and status >= 500:
        return ProviderError(
            f"FreeModel had a server error (HTTP {status}). Please try again.",
            transient=True,
        )
    return ProviderError(f"FreeModel error: {detail}")
