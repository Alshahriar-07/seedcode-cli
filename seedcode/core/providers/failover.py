"""Automatic provider failover (v7.2.5).

When more than one usable provider configuration is available, a request that
fails on the active provider retries there first, then fails over to the next
healthy one — without restarting the task. The chat engine's caller (a Code
Mode session) keeps issuing the same prompt, and the conversation history is
preserved, so the work resumes at the current operation instead of from zero.

This module only *chooses* the next provider. The switch itself (writing
``config.active_provider`` / the provider's model) is done by the caller, so
the task engine stays in control of its own state.

Health is consulted so a configuration that just failed is not immediately
retried, and a permanently rejected key is never used again in the session.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import PROVIDERS, ProviderError, get_provider, provider_ready
from . import health as health_mod
from .base import Provider


@dataclass(frozen=True, slots=True)
class Candidate:
    """A provider configuration that can answer a request."""

    provider_id: str
    model: str


class FailoverChain:
    """Ordered, health-aware provider candidates for the active config."""

    def __init__(self, config, tracker: health_mod.HealthTracker | None = None) -> None:
        self.config = config
        self.health = tracker or health_mod.tracker()

    # --- candidate discovery -------------------------------------------------
    def _ordered_ids(self) -> list[str]:
        from . import visible_provider_ids

        active = (self.config.provider or "").lower()
        ids = [active]
        ids.extend(visible_provider_ids(self.config))
        seen: list[str] = []
        for pid in ids:
            pid = (pid or "").lower()
            if pid and pid not in seen:
                seen.append(pid)
        return seen

    def _provider(self, provider_id: str) -> Provider | None:
        if provider_id not in PROVIDERS:
            try:
                get_provider(provider_id)
            except ProviderError:
                return None
        return PROVIDERS.get(provider_id)

    def _model_for(self, provider_id: str) -> str:
        entry = self.config.custom_provider(provider_id)
        if entry is not None:
            return (entry.model or "").strip()
        provider_config = self.config.providers.get(provider_id)
        return (provider_config.model or "").strip() if provider_config else ""

    def _usable(self, provider_id: str) -> bool:
        if not self.health.available(provider_id):
            return False  # cooling down or permanently rejected
        if self._provider(provider_id) is None:
            return False
        entry = self.config.custom_provider(provider_id)
        if entry is not None:
            return bool(entry.enabled and entry.api_key.strip())
        return provider_ready(provider_id, self.config)

    def candidates(self, *, exclude=()) -> list[Candidate]:
        """Usable candidates, active provider first, ``exclude`` omitted."""
        excluded = {(p or "").lower() for p in exclude}
        found: list[Candidate] = []
        for pid in self._ordered_ids():
            if pid in excluded or not self._usable(pid):
                continue
            model = self._model_for(pid)
            if not model:
                continue
            found.append(Candidate(pid, model))
        active = (self.config.provider or "").lower()
        found.sort(key=lambda c: 0 if c.provider_id == active else 1)
        return found

    def next_candidate(self, tried) -> Candidate | None:
        """The next provider to try, skipping everything already tried."""
        for candidate in self.candidates(exclude=tried):
            return candidate
        return None


__all__ = ["Candidate", "FailoverChain"]
