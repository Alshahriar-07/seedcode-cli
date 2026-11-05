"""v10.2.0 management-surface contract.

Locks in the pieces the TUI now exposes as real controls:

* the ``/models`` command exists and its fallback/profile helpers mutate only
  the fields they own;
* automatic provider failover updates both persisted config and the live
  engine;
* FreeModel Claude, FreeModel Codex (Responses) and AeroLink report an
  output-token finish reason instead of silently ending a truncated stream.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from seedcode.commands import _REGISTRY
from seedcode.commands.models import (
    add_fallback_model,
    clear_fallback_models,
    pin_model_profile,
    remove_fallback_model,
)
from seedcode.commands.provider import set_auto_failover
from seedcode.core.models import AppConfig
from seedcode.core.providers.aerolink import _iter_sse_events as aero_events
from seedcode.core.providers.base import FinishEvent, TextDelta
from seedcode.core.providers.freemodel import _iter_claude_events as claude_events


class FakeSSEResponse:
    """The minimal ``iter_lines`` surface the Anthropic parsers consume."""

    def __init__(self, events: list[dict]) -> None:
        self._events = events

    def iter_lines(self):
        for event in self._events:
            yield "data: " + json.dumps(event)


@pytest.mark.parametrize("parser", [aero_events, claude_events])
class TestFinishReasonParity:
    def test_max_tokens_is_reported(self, parser) -> None:
        events = list(
            parser(
                FakeSSEResponse(
                    [
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "partial"},
                        },
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "max_tokens"},
                        },
                    ]
                )
            )
        )
        assert events[0] == TextDelta("partial")
        assert events[-1] == FinishEvent("max_tokens")
        assert events[-1].truncated

    def test_natural_end_is_not_truncation(self, parser) -> None:
        events = list(
            parser(
                FakeSSEResponse(
                    [
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn"},
                        }
                    ]
                )
            )
        )
        assert not any(isinstance(event, FinishEvent) for event in events)


class TestModelManagement:
    def test_models_command_is_registered(self) -> None:
        assert "models" in _REGISTRY
        assert "model" in _REGISTRY  # the simple picker remains available
        assert "Manage models" in _REGISTRY["models"][1]

    def test_fallback_helpers_are_ordered_and_idempotent(self) -> None:
        config = AppConfig(provider="openrouter", model="primary/model")
        ok, _ = add_fallback_model(config, "backup/one")
        assert ok and config.fallback_models == ["backup/one"]
        ok, _ = add_fallback_model(config, "backup/two")
        assert ok and config.fallback_models == ["backup/one", "backup/two"]
        # A fallback can never be the active model or a duplicate.
        assert add_fallback_model(config, "primary/model")[0] is False
        assert add_fallback_model(config, "backup/one")[0] is False
        assert remove_fallback_model(config, "backup/one") is True
        assert config.fallback_models == ["backup/two"]
        assert remove_fallback_model(config, "missing") is False
        assert clear_fallback_models(config) == 1
        assert config.fallback_models == []

    def test_profile_pin_targets_one_model(self) -> None:
        config = AppConfig(provider="ollama", model="qwen3:4b")
        assert pin_model_profile(config, "qwen3:4b", "advanced") is True
        assert config.model_profiles == {"qwen3:4b": "advanced"}
        # Invalid values never create a broken mapping.
        assert pin_model_profile(config, "qwen3:4b", "nonsense") is False
        assert pin_model_profile(config, "", "small") is False
        assert config.model_profiles == {"qwen3:4b": "advanced"}


class TestProviderFailoverControl:
    def test_toggle_updates_config_and_live_engine(self) -> None:
        config = AppConfig(provider="openrouter", model="x")
        engine = SimpleNamespace(failover_enabled=False)
        set_auto_failover(config, True, engine, persist=False)
        assert config.auto_failover is True
        assert engine.failover_enabled is True
        set_auto_failover(config, False, engine, persist=False)
        assert config.auto_failover is False
        assert engine.failover_enabled is False
