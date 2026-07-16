"""OpenRouter backend: OpenAI-compatible chat completions.

Model listing uses OpenRouter's public catalogue and keeps only FREE models
(zero prompt and completion pricing), per product requirements. Nothing is
hardcoded — the user picks from the live list.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from openai import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from ..streaming import iter_stream
from ...utils.logger import get_logger
from .base import ModelInfo, Provider, ProviderError, ValidationResult

if TYPE_CHECKING:
    from ..models import AppConfig, Message

_log = get_logger("openrouter")

_BASE_URL = "https://openrouter.ai/api/v1"
_VALIDATE_URL = f"{_BASE_URL}/key"
_MODELS_URL = f"{_BASE_URL}/models"
_TIMEOUT = 20.0
# Chat: fail fast on connect, but give slow free-tier models time to answer.
_CHAT_TIMEOUT = httpx.Timeout(20.0, read=180.0)
# Sent so Seed Code shows up correctly in OpenRouter dashboards / rankings.
_HEADERS = {
    "HTTP-Referer": "https://github.com/Alshahriar-07/seedbot-cli",
    "X-Title": "Seed Code",
}


def _is_free(entry: dict[str, Any]) -> bool:
    """True when both prompt and completion pricing are exactly zero."""
    pricing = entry.get("pricing") or {}
    try:
        return float(pricing.get("prompt", 1)) == 0.0 and float(
            pricing.get("completion", 1)
        ) == 0.0
    except (TypeError, ValueError):
        return False


@dataclass
class OpenRouterProvider(Provider):
    def __post_init__(self) -> None:
        self.id = "openrouter"
        self.label = "OpenRouter"
        self.requires_key = True
        self.key_hint = "sk-or-...  (create one at https://openrouter.ai/keys)"

    def validate_key(self, api_key: str) -> ValidationResult:
        key = api_key.strip()
        if not key:
            return ValidationResult(False, "API key is empty.")
        if not key.startswith("sk-or-"):
            return ValidationResult(
                False, "That does not look like an OpenRouter key (expected 'sk-or-...')."
            )
        try:
            response = httpx.get(
                _VALIDATE_URL, headers={"Authorization": f"Bearer {key}"}, timeout=_TIMEOUT
            )
        except httpx.TimeoutException:
            return ValidationResult(False, "Validation timed out. Check your connection.")
        except httpx.HTTPError:
            return ValidationResult(False, "Could not reach OpenRouter. Check your connection.")
        if response.status_code == 200:
            return ValidationResult(True, "API key verified.")
        if response.status_code in (401, 403):
            return ValidationResult(False, "API key was rejected by OpenRouter.")
        return ValidationResult(
            False, f"Unexpected response from OpenRouter (HTTP {response.status_code})."
        )

    def list_models(self, config: "AppConfig") -> list[ModelInfo]:
        try:
            response = httpx.get(_MODELS_URL, timeout=_TIMEOUT)
            response.raise_for_status()
            data = response.json().get("data", [])
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Timed out fetching the OpenRouter model list.", transient=True
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(
                "Could not fetch the OpenRouter model list. Check your connection.",
                transient=True,
            ) from exc

        models = [
            ModelInfo(
                id=entry["id"],
                label=entry.get("name") or entry["id"],
                detail=f"{entry.get('context_length') or '?'} ctx",
            )
            for entry in data
            if entry.get("id") and _is_free(entry)
        ]
        models.sort(key=lambda m: m.id)
        if not models:
            raise ProviderError("OpenRouter reported no free models right now.")
        return models

    def stream_chat(self, config: "AppConfig", messages: list["Message"]) -> Iterator[str]:
        client = OpenAI(
            api_key=config.get_api_key("openrouter"),
            base_url=_BASE_URL,
            default_headers=_HEADERS,
            timeout=_CHAT_TIMEOUT,
            # The engine owns retry policy; keep the SDK from stacking its own.
            max_retries=0,
        )
        max_tokens = config.effective_max_tokens()
        _log.debug("chat request: model=%s max_tokens=%d", config.model, max_tokens)
        try:
            stream = client.chat.completions.create(
                model=config.model,
                messages=[m.to_api() for m in messages],  # type: ignore[arg-type]
                # Clamped budget — an unbounded request makes OpenRouter derive
                # max_tokens from the model's context window (e.g. 65536),
                # which free-tier accounts reject with HTTP 402.
                max_tokens=max_tokens,
                stream=True,
            )
            yield from iter_stream(stream)
        except AuthenticationError as exc:
            raise ProviderError(
                "Authentication failed. Your OpenRouter key may be invalid — run /provider."
            ) from exc
        except RateLimitError as exc:
            raise ProviderError(
                "Rate limited by OpenRouter. Please wait and try again.", transient=True
            ) from exc
        except APIConnectionError as exc:
            # Includes read timeouts — the SDK wraps them in connection errors.
            raise ProviderError(
                "Network error reaching OpenRouter. Check your connection.", transient=True
            ) from exc
        except APIError as exc:
            raise _friendly_api_error(exc, config.model) from exc


def _friendly_api_error(exc: APIError, model: str) -> ProviderError:
    """Translate OpenRouter API errors into actionable user messages."""
    status = getattr(exc, "status_code", None)
    detail = getattr(exc, "message", str(exc)) or "Unknown API error."
    if status == 402:
        return ProviderError(
            "OpenRouter rejected the request for lack of credits (HTTP 402). "
            "Try a ':free' model via /model, or add credits at openrouter.ai."
        )
    if status == 404:
        return ProviderError(
            f"Model '{model}' was not found on OpenRouter. Pick another with /model."
        )
    if status is not None and status >= 500:
        return ProviderError(
            "OpenRouter had a server error. Please try again.", transient=True
        )
    return ProviderError(f"OpenRouter error: {detail}")
