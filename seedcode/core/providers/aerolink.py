"""AeroLink backend: Anthropic-compatible gateway at capi.aerolink.lat.

AeroLink (https://aerolink.lat) exposes the Anthropic Messages API and
serves Claude-family models only. This provider speaks that protocol
directly over httpx: ``POST /v1/messages`` with SSE streaming, and
``GET /v1/models`` for the catalogue when the gateway supports it. No
models are hardcoded; if listing is unavailable the user types the model
ID from their AeroLink dashboard. Independent API key and model.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from .base import ModelInfo, Provider, ProviderError, ValidationResult

if TYPE_CHECKING:
    from ..models import AppConfig, Message

_BASE_URL = "https://capi.aerolink.lat"
_API_VERSION = "2023-06-01"  # Anthropic Messages API version header
_MAX_TOKENS = 4096  # required request parameter for the Messages API
_TIMEOUT = httpx.Timeout(20.0, read=180.0)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": _API_VERSION,
        "content-type": "application/json",
    }


@dataclass
class AeroLinkProvider(Provider):
    def __post_init__(self) -> None:
        self.id = "aerolink"
        self.label = "AeroLink"
        self.base_url = _BASE_URL
        self.requires_key = True
        self.key_hint = "from your dashboard at https://aerolink.lat (API keys page)"

    def validate_key(self, api_key: str) -> ValidationResult:
        key = api_key.strip()
        if not key:
            return ValidationResult(False, "API key is empty.")
        try:
            response = httpx.get(
                f"{_BASE_URL}/v1/models", headers=_headers(key), timeout=20.0
            )
        except httpx.TimeoutException:
            return ValidationResult(False, "Validation timed out. Check your connection.")
        except httpx.HTTPError:
            return ValidationResult(False, "Could not reach AeroLink. Check your connection.")
        if response.status_code == 200:
            return ValidationResult(True, "API key verified.")
        if response.status_code in (401, 403):
            return ValidationResult(False, "API key was rejected by AeroLink.")
        # Some gateways don't proxy /v1/models; accept and verify on first chat.
        return ValidationResult(
            True, "Key saved. AeroLink did not confirm it; it will be verified on first message."
        )

    def list_models(self, config: "AppConfig") -> list[ModelInfo]:
        key = config.get_api_key("aerolink")
        try:
            response = httpx.get(
                f"{_BASE_URL}/v1/models", headers=_headers(key), timeout=20.0
            )
            response.raise_for_status()
            data = response.json().get("data", [])
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Timed out fetching the AeroLink model list.", transient=True
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(
                "AeroLink did not return a model list. Enter a model ID from your "
                "dashboard with: /model <model-id>",
            ) from exc

        # AeroLink serves the Claude family only — filter anything else out.
        models = [
            ModelInfo(id=entry["id"], label=entry.get("display_name") or entry["id"])
            for entry in data
            if entry.get("id") and "claude" in entry["id"].lower()
        ]
        if not models:
            raise ProviderError(
                "AeroLink returned no Claude-family models. Enter a model ID from "
                "your dashboard with: /model <model-id>"
            )
        return models

    def stream_chat(self, config: "AppConfig", messages: list["Message"]) -> Iterator[str]:
        # The Messages API takes the system prompt as a top-level field.
        system = next((m.content for m in messages if m.role == "system"), None)
        turns = [m.to_api() for m in messages if m.role != "system"]
        payload: dict[str, object] = {
            "model": config.model,
            "max_tokens": _MAX_TOKENS,
            "messages": turns,
            "stream": True,
        }
        if system:
            payload["system"] = system

        try:
            with httpx.stream(
                "POST",
                f"{_BASE_URL}/v1/messages",
                headers=_headers(config.get_api_key("aerolink")),
                json=payload,
                timeout=_TIMEOUT,
            ) as response:
                if response.status_code in (401, 403):
                    raise ProviderError(
                        "Authentication failed. Your AeroLink key may be invalid — run /apikey."
                    )
                if response.status_code == 402:
                    raise ProviderError(
                        "AeroLink says the account is out of credits (HTTP 402). "
                        "Check your plan at https://aerolink.lat."
                    )
                if response.status_code == 404:
                    raise ProviderError(
                        f"Model '{config.model}' was not found on AeroLink — run /model."
                    )
                if response.status_code == 408:
                    raise ProviderError(
                        "AeroLink timed out handling the request. Please try again.",
                        transient=True,
                    )
                if response.status_code == 429:
                    raise ProviderError(
                        "Rate limited by AeroLink. Please wait and try again.", transient=True
                    )
                if response.status_code >= 500:
                    raise ProviderError(
                        f"AeroLink had a server error (HTTP {response.status_code}). "
                        "Please try again.",
                        transient=True,
                    )
                if response.status_code >= 400:
                    detail = response.read().decode("utf-8", "replace")[:300]
                    raise ProviderError(f"AeroLink error (HTTP {response.status_code}): {detail}")
                yield from _iter_sse_text(response)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Timed out talking to AeroLink. Please try again.", transient=True
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Network error reaching AeroLink. Check your connection.", transient=True
            ) from exc


def _iter_sse_text(response: httpx.Response) -> Iterator[str]:
    """Yield text deltas from an Anthropic-style SSE stream."""
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
            message = (event.get("error") or {}).get("message", "Unknown AeroLink error.")
            raise ProviderError(f"AeroLink error: {message}")
