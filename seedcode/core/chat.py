"""Chat engine: routes conversations through the active provider.

The engine owns conversation state and retry policy. Which vendor actually
answers is decided per request by ``config.provider`` + ``config.model``, so
switching providers or models mid-session takes effect on the next turn.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from .models import AppConfig, Message
from .providers import ProviderError, get_provider
from ..utils.logger import get_logger

_log = get_logger("chat")

# Transparent retry for transient failures before any output has streamed.
_MAX_RETRIES = 2
_RETRY_BACKOFF_S = 1.5

SYSTEM_PROMPT = (
    "You are Seed Code, a fast, minimal, developer-focused AI coding assistant. "
    "Be concise and professional. Prefer clear, correct code with short "
    "explanations. Use markdown fenced code blocks with language hints."
)


class ChatError(Exception):
    """Raised with a user-friendly message when a request cannot complete."""


class ChatEngine:
    """Stateful conversation manager delegating requests to providers."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.messages: list[Message] = [Message(role="system", content=SYSTEM_PROMPT)]

    # --- history management ------------------------------------------------
    def add_user(self, content: str) -> None:
        self.messages.append(Message(role="user", content=content))

    def add_assistant(self, content: str) -> None:
        self.messages.append(Message(role="assistant", content=content))

    def drop_last_user(self) -> None:
        """Remove a trailing unanswered user turn (after a failed request).

        Keeps the transcript alternating so the next attempt never sends two
        consecutive user messages, which strict APIs reject.
        """
        if self.messages and self.messages[-1].role == "user":
            self.messages.pop()

    def reset(self) -> None:
        """Clear the conversation but keep the system prompt."""
        self.messages = [self.messages[0]]

    @property
    def transcript(self) -> list[Message]:
        return self.messages

    # --- requests ----------------------------------------------------------
    def stream_reply(self) -> Iterator[str]:
        """Stream a reply via the current provider + current model.

        Transient provider failures are retried with a short backoff — but
        never once output has started, since a retry would replay the reply.
        All failures surface as :class:`ChatError` with friendly text.
        """
        if not self.config.model:
            raise ChatError("No model selected. Pick one with /model first.")

        try:
            provider = get_provider(self.config.provider)
        except ProviderError as exc:
            raise ChatError(str(exc)) from exc

        attempt = 0
        while True:
            yielded = False
            _log.info(
                "request: provider=%s model=%s turns=%d attempt=%d",
                self.config.provider,
                self.config.model,
                len(self.messages),
                attempt,
            )
            try:
                for piece in provider.stream_chat(self.config, self.messages):
                    yielded = True
                    yield piece
                _log.info("request complete: provider=%s", self.config.provider)
                return
            except ProviderError as exc:
                if exc.transient and not yielded and attempt < _MAX_RETRIES:
                    attempt += 1
                    _log.warning("transient failure, retry %d: %s", attempt, exc)
                    time.sleep(_RETRY_BACKOFF_S * attempt)
                    continue
                _log.error("request failed: %s", exc)
                raise ChatError(str(exc)) from exc
            except Exception as exc:  # last-resort guard: never crash the REPL
                _log.exception("unexpected error during request")
                raise ChatError(f"Unexpected error: {exc}") from exc
