"""Provider abstraction: the contract every AI backend implements.

A provider knows how to validate credentials, list available models, and
stream a chat reply. The chat engine and the UI never talk to a vendor API
directly — they go through this interface, so adding a backend means adding
one module, not touching the app.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import AppConfig, Message


class ProviderError(Exception):
    """A provider request failed with a user-presentable message.

    ``transient`` marks failures worth retrying automatically (timeouts,
    connection drops, rate limits) as opposed to permanent ones (bad key,
    unknown model).
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass(slots=True)
class ValidationResult:
    """Outcome of an API key validation attempt."""

    ok: bool
    message: str


@dataclass(slots=True)
class ModelInfo:
    """One selectable model as reported by a provider."""

    id: str
    label: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.id


@dataclass
class Provider(ABC):
    """Base class for AI backends."""

    id: str = field(init=False)
    label: str = field(init=False)
    requires_key: bool = field(init=False, default=True)
    key_hint: str = field(init=False, default="")

    @abstractmethod
    def validate_key(self, api_key: str) -> ValidationResult:
        """Check credentials. Providers without keys validate connectivity."""

    @abstractmethod
    def list_models(self, config: "AppConfig") -> list[ModelInfo]:
        """Fetch the models the user may select. Raises ProviderError."""

    @abstractmethod
    def stream_chat(self, config: "AppConfig", messages: list["Message"]) -> Iterator[str]:
        """Stream a reply for the conversation. Raises ProviderError."""
