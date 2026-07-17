"""Ollama backend: talks to a local Ollama server.

Uses the native Ollama HTTP API: ``GET /api/tags`` to detect the server and
list installed models, ``POST /api/chat`` (NDJSON stream) for replies. No API
key is involved; the server address is configurable (``ollama_host``).
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

_DETECT_TIMEOUT = 5.0
# Local generation can pause while a model loads into memory; be generous.
_CHAT_TIMEOUT = httpx.Timeout(10.0, read=300.0)


def _not_running(host: str) -> ProviderError:
    return ProviderError(
        f"Ollama is not reachable at {host}. Start it with 'ollama serve' "
        "(install from https://ollama.com) and try again."
    )


@dataclass
class OllamaProvider(Provider):
    def __post_init__(self) -> None:
        self.id = "ollama"
        self.label = "Ollama (local)"
        self.base_url = ""  # per-user host lives in config.ollama_host
        self.requires_key = False
        self.key_hint = ""

    def validate_key(self, api_key: str) -> ValidationResult:
        """No key needed; 'validation' means the local server responds."""
        return ValidationResult(True, "Ollama needs no API key.")

    def extra_settings(self, config: "AppConfig") -> dict[str, str]:
        return {"host": config.ollama_host}

    def set_extra_setting(
        self, config: "AppConfig", name: str, value: str
    ) -> tuple[bool, str]:
        if name != "host":
            return False, f"{self.label} has no setting '{name}'."
        host = value.strip().rstrip("/")
        if not host.startswith(("http://", "https://")):
            return False, "host expects a URL like http://localhost:11434."
        config.ollama_host = host
        return True, f"Ollama host set to {host}."

    def detect(self, config: "AppConfig") -> bool:
        """True when the Ollama server answers on the configured host."""
        try:
            response = httpx.get(f"{config.ollama_host}/api/tags", timeout=_DETECT_TIMEOUT)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self, config: "AppConfig") -> list[ModelInfo]:
        try:
            response = httpx.get(f"{config.ollama_host}/api/tags", timeout=_DETECT_TIMEOUT)
            response.raise_for_status()
            entries = response.json().get("models", [])
        except httpx.HTTPError as exc:
            raise _not_running(config.ollama_host) from exc
        except ValueError as exc:
            raise ProviderError("Ollama returned an unreadable model list.") from exc

        models = []
        for entry in entries:
            name = entry.get("name")
            if not name:
                continue
            size = entry.get("size") or 0
            detail = f"{size / 1e9:.1f} GB" if size else ""
            models.append(ModelInfo(id=name, detail=detail))
        if not models:
            raise ProviderError(
                "Ollama is running but has no models installed. "
                "Pull one first, e.g.: ollama pull llama3.2"
            )
        return models

    def stream_chat(self, config: "AppConfig", messages: list["Message"]) -> Iterator[str]:
        payload = {
            "model": config.model,
            "messages": [m.to_api() for m in messages],
            "stream": True,
        }
        try:
            with httpx.stream(
                "POST", f"{config.ollama_host}/api/chat", json=payload, timeout=_CHAT_TIMEOUT
            ) as response:
                if response.status_code == 404:
                    raise ProviderError(
                        f"Model '{config.model}' is not installed in Ollama. "
                        f"Run: ollama pull {config.model}  (or pick another via /model)"
                    )
                if response.status_code >= 500:
                    raise ProviderError(
                        f"Ollama had a server error (HTTP {response.status_code}). "
                        "Please try again.",
                        transient=True,
                    )
                if response.status_code >= 400:
                    detail = response.read().decode("utf-8", "replace")[:300]
                    raise ProviderError(f"Ollama error (HTTP {response.status_code}): {detail}")
                # NDJSON: one JSON object per line until "done": true.
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("error"):
                        raise ProviderError(f"Ollama error: {event['error']}")
                    piece = (event.get("message") or {}).get("content")
                    if piece:
                        yield piece
                    if event.get("done"):
                        return
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Timed out waiting for Ollama. The model may still be loading — try again.",
                transient=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise _not_running(config.ollama_host) from exc
