"""Provider health management (v7.2.5).

A provider is more than a name and a key: it can be healthy, mid-retry,
temporarily down, rate-limited, or permanently rejected. This module records
that health so the engine can make a real decision instead of hammering a
configuration that just failed:

* a transient server error or timeout → retry the same provider;
* a rate limit → cooldown, then retry (or switch when another is available);
* a rejected API key → stop retrying that configuration entirely and switch;
* a network failure → reconnect/retry.

Health is session-only (never persisted) and bounded: every state carries a
cooldown, so there is no path to an infinite retry loop. The tracker stores no
credentials — only the state, a short non-secret reason, and timestamps.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Callable


class HealthState(str, enum.Enum):
    """The states a provider configuration can be in."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    RETRYING = "retrying"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_ERROR = "authentication_error"
    OFFLINE = "offline"


#: A rejection that will never succeed on retry (bad/revoked key).
PERMANENT_STATES = frozenset({HealthState.AUTHENTICATION_ERROR})

#: States that are worth trying again once their cooldown elapses.
RETRYABLE_STATES = frozenset(
    {
        HealthState.RETRYING,
        HealthState.TEMPORARILY_UNAVAILABLE,
        HealthState.RATE_LIMITED,
        HealthState.OFFLINE,
    }
)

#: Default cooldown (seconds) applied after a failure, per state. ``None``
#: means "never auto-retry this configuration".
_COOLDOWNS: dict[HealthState, float | None] = {
    HealthState.UNKNOWN: 0.0,
    HealthState.HEALTHY: 0.0,
    HealthState.RETRYING: 2.0,
    HealthState.TEMPORARILY_UNAVAILABLE: 10.0,
    HealthState.RATE_LIMITED: 30.0,
    HealthState.OFFLINE: 10.0,
    HealthState.AUTHENTICATION_ERROR: None,
}

_AUTH_TOKENS = (
    "api key",
    "authentication",
    "unauthorized",
    "invalid key",
    "rejected",
    "401",
    "403",
)
_OFFLINE_TOKENS = ("not reachable", "not running", "connection", "network", "timed out")


def classify_error(exc: BaseException | None) -> HealthState:
    """Map a provider failure to a :class:`HealthState`.

    Reads ``ProviderError.transient`` when present (the provider's own
    classification) and otherwise falls back to the message. Never raises and
    never inspects credentials.
    """
    transient = bool(getattr(exc, "transient", False))
    message = str(exc or "").lower()
    if not transient:
        if any(token in message for token in _AUTH_TOKENS):
            return HealthState.AUTHENTICATION_ERROR
        if any(token in message for token in _OFFLINE_TOKENS):
            return HealthState.OFFLINE
        return HealthState.TEMPORARILY_UNAVAILABLE
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None or "429" in message or "rate limit" in message:
        return HealthState.RATE_LIMITED
    if any(token in message for token in _OFFLINE_TOKENS):
        return HealthState.OFFLINE
    return HealthState.RETRYING


@dataclass
class ProviderHealth:
    """The tracked health of one provider configuration."""

    state: HealthState = HealthState.UNKNOWN
    failures: int = 0
    successes: int = 0
    last_error: str = ""
    updated_at: float = 0.0
    retry_at: float = 0.0

    def available(self, now: float) -> bool:
        """Whether this configuration may be used right now."""
        if self.state in PERMANENT_STATES:
            return False
        return now >= self.retry_at

    def cooldown_remaining(self, now: float) -> float:
        return max(0.0, self.retry_at - now)


@dataclass
class HealthTracker:
    """Session-only health for every provider id (bounded, no retry loops)."""

    clock: Callable[[], float] = time.monotonic
    _health: dict[str, ProviderHealth] = field(default_factory=dict)

    # --- reads ---------------------------------------------------------------
    def get(self, provider_id: str) -> ProviderHealth:
        return self._health.setdefault(
            (provider_id or "").lower(), ProviderHealth()
        )

    def state(self, provider_id: str) -> HealthState:
        return self.get(provider_id).state

    def available(self, provider_id: str) -> bool:
        """Whether the provider may be used now (permanent failures blocked)."""
        return self.get(provider_id).available(self.clock())

    def cooldown_remaining(self, provider_id: str) -> float:
        return self.get(provider_id).cooldown_remaining(self.clock())

    def snapshot(self) -> dict[str, str]:
        """``{provider_id: state}`` for display/diagnostics."""
        return {pid: health.state.value for pid, health in self._health.items()}

    # --- writes --------------------------------------------------------------
    def record_success(self, provider_id: str) -> ProviderHealth:
        health = self.get(provider_id)
        health.state = HealthState.HEALTHY
        health.successes += 1
        health.failures = 0
        health.last_error = ""
        health.retry_at = 0.0
        health.updated_at = self.clock()
        return health

    def record_failure(
        self,
        provider_id: str,
        exc: BaseException | None = None,
        *,
        state: HealthState | None = None,
        reason: str = "",
        retry_after: float | None = None,
        cooldown: float | None = None,
    ) -> ProviderHealth:
        """Record a failure, set the cooldown, and return the health.

        ``cooldown`` overrides the state default; pass ``0.0`` to allow an
        immediate retry (e.g. the engine's first in-place retry).
        """
        health = self.get(provider_id)
        resolved = state or classify_error(exc)
        health.state = resolved
        health.failures += 1
        health.last_error = _short(reason or str(exc or ""))
        health.updated_at = self.clock()
        if resolved in PERMANENT_STATES:
            health.retry_at = float("inf")
            return health
        wait = _COOLDOWNS.get(resolved, 0.0) if cooldown is None else max(0.0, cooldown)
        if retry_after is not None and retry_after > 0:
            wait = max(wait, float(retry_after))
        health.retry_at = self.clock() + (wait or 0.0)
        return health

    def reset(self, provider_id: str | None = None) -> None:
        """Clear one provider's health (or all of it)."""
        if provider_id is None:
            self._health.clear()
        else:
            self._health.pop((provider_id or "").lower(), None)


def _short(text: str, limit: int = 160) -> str:
    """A one-line, non-secret description for display."""
    return " ".join((text or "").split())[:limit]


#: Process-wide tracker used by the chat engine and the failover chain.
_TRACKER = HealthTracker()


def tracker() -> HealthTracker:
    """The process-wide health tracker."""
    return _TRACKER


def reset() -> None:
    """Test isolation hook."""
    _TRACKER.reset()


__all__ = [
    "HealthState",
    "HealthTracker",
    "PERMANENT_STATES",
    "RETRYABLE_STATES",
    "ProviderHealth",
    "classify_error",
    "reset",
    "tracker",
]
