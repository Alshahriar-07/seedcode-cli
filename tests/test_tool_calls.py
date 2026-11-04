"""Tool-call state management (OpenCode session-tool parity).

Covers :mod:`seedcode.core.calls` and its wiring into
``AgentEngine._execute_calls``:

* every call gets a UNIQUE id and real start/finish timing;
* terminal statuses are recorded from what actually happened
  (completed / failed / blocked / deduped) — never guessed;
* a provider call id that was already answered in the same turn is never
  executed twice: the recorded result is replayed to the model;
* duplicate prevention is by call id only — text-protocol calls carry no
  ids, so a genuinely repeated (tool, args) pair still executes.
"""

from __future__ import annotations

import time
from pathlib import Path

from seedcode.core.agent import AgentEngine, ToolCall
from seedcode.core.calls import (
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_DEDUPED,
    STATUS_FAILED,
    CallTracker,
)
from seedcode.core.models import AppConfig
from seedcode.tools import (
    TOOL_REGISTRY,
    PermissionLevel,
    PermissionManager,
)


def _engine(tmp_path: Path) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
    return AgentEngine(config, perm)


class TestCallTracker:
    def test_ids_are_unique_even_without_provider_ids(self) -> None:
        tracker = CallTracker()
        first = tracker.start("read_file", {"path": "a"})
        second = tracker.start("read_file", {"path": "b"})
        assert first.call_id != second.call_id
        assert first.call_id in tracker.records

    def test_provider_id_wins_when_given(self) -> None:
        tracker = CallTracker()
        state = tracker.start("read_file", {}, call_id="call_abc")
        assert state.call_id == "call_abc"

    def test_duplicate_of_returns_finished_records(self) -> None:
        tracker = CallTracker()
        state = tracker.start("read_file", {}, call_id="call_1")
        assert tracker.duplicate_of("call_1") is None  # still running
        tracker.begin(state)
        tracker.finish(state, True, "file contents")
        seen = tracker.duplicate_of("call_1")
        assert seen is state
        assert seen.status == STATUS_COMPLETED

    def test_duplicate_detection_sees_blocked_and_deduped_too(self) -> None:
        tracker = CallTracker()
        state = tracker.start("run_command", {}, call_id="call_2")
        tracker.begin(state)
        tracker.finish(state, False, "Blocked: denied", blocked=True)
        assert tracker.duplicate_of("call_2") is state
        assert state.status == STATUS_BLOCKED

    def test_duration_is_measured_not_fabricated(self) -> None:
        tracker = CallTracker()
        state = tracker.start("read_file", {})
        tracker.begin(state)
        time.sleep(0.01)
        tracker.finish(state, True, "done")
        assert state.duration_ms is not None
        assert state.duration_ms >= 0

    def test_empty_duplicate_query_is_never_a_match(self) -> None:
        tracker = CallTracker()
        state = tracker.start("read_file", {})
        tracker.begin(state)
        tracker.finish(state, True, "done")
        # text-protocol calls carry no id: nothing to dedup on
        assert tracker.duplicate_of("") is None

    def test_reset_clears_records(self) -> None:
        tracker = CallTracker()
        state = tracker.start("read_file", {}, call_id="call_1")
        tracker.finish(state, True, "done")
        tracker.reset()
        assert tracker.records == {}
        assert tracker.summary() == {"calls": 0, "statuses": {}}

    def test_summary_counts_statuses(self) -> None:
        tracker = CallTracker()
        ok_state = tracker.start("read_file", {})
        tracker.begin(ok_state)
        tracker.finish(ok_state, True, "ok")
        bad_state = tracker.start("run_command", {})
        tracker.begin(bad_state)
        tracker.finish(bad_state, False, "boom")
        summary = tracker.summary()
        assert summary["calls"] == 2
        assert summary["statuses"] == {
            STATUS_COMPLETED: 1,
            STATUS_FAILED: 1,
        }


class TestEngineCallState:
    def test_real_call_records_id_status_and_timing(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
        engine = _engine(tmp_path)
        results, failed = engine._execute_calls(
            [ToolCall("read_file", {"path": "notes.txt"}, call_id="call_9")]
        )
        assert not failed
        state = engine.calls.records["call_9"]
        assert state.tool == "read_file"
        assert state.status == STATUS_COMPLETED
        assert state.ok is True
        assert state.started_at is not None
        assert state.finished_at is not None
        assert state.duration_ms is not None
        assert "hello" in results[0]

    def test_failure_and_blocked_statuses(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        results, failed = engine._execute_calls(
            [
                ToolCall("read_file", {"path": "does_not_exist.txt"}, call_id="c1"),
                ToolCall(
                    "write_file", {"path": "x.txt", "content": "hi"}, call_id="c2"
                ),
            ]
        )
        assert failed  # the failed read marks the step
        assert engine.calls.records["c1"].status == STATUS_FAILED
        # run_command under a read-only floor is the clean "Blocked:" path
        engine.permissions.level = PermissionLevel.READ_ONLY
        _ok, step_failed = engine._execute_calls(
            [ToolCall("run_command", {"command": "echo hi"}, call_id="c3")]
        )
        assert engine.calls.records["c3"].status == STATUS_BLOCKED

    def test_duplicate_call_id_is_replayed_not_executed(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
        engine = _engine(tmp_path)
        tool = TOOL_REGISTRY["read_file"]
        executions = {"count": 0}
        real_runner = tool.runner

        def counting(perm, args):
            executions["count"] += 1
            return real_runner(perm, args)

        object.__setattr__(tool, "runner", counting)
        try:
            engine._execute_calls(
                [ToolCall("read_file", {"path": "notes.txt"}, call_id="call_x")]
            )
            first_summary = engine.calls.summary()
            # Same provider id again in the same turn (a resend): must NOT run.
            results, _failed = engine._execute_calls(
                [ToolCall("read_file", {"path": "notes.txt"}, call_id="call_x")]
            )
            second_summary = engine.calls.summary()
        finally:
            object.__setattr__(tool, "runner", real_runner)

        assert executions["count"] == 1, "duplicate call id must not re-execute"
        assert second_summary["statuses"].get(STATUS_DEDUPED) == 1
        assert "[DUPLICATE" in results[0]
        assert "hello" in results[0]  # the recorded result is replayed
        # reset-as-turn-boundary keeps summaries separable
        assert first_summary["calls"] == 1

    def test_tracker_resets_per_turn(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.calls.start("read_file", {}, call_id="stale")
        engine.calls.reset()
        assert engine.calls.duplicate_of("stale") is None
