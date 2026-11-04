"""Subagent delegation (OpenCode subagent-tool parity).

Reference: ``OPENCODE/tmdp/packages/core/src/tool/plugin/subagent.ts``. The
``subagent`` tool must really spawn a FRESH child engine — isolated message
history, its own profile, shared permission manager — and return only the
child's final report. A child may not spawn further subagents (depth guard),
failures isolate to a plain tool error, and a profile's optionally-declared
provider/model routes the child while leaving the session configuration
untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedcode.core.agent import AgentEngine, ToolCall
from seedcode.core.agents import AgentProfile, describe
from seedcode.core.calls import STATUS_COMPLETED
from seedcode.core.models import AppConfig
from seedcode.tools import (
    PermissionLevel,
    PermissionManager,
    get_tool,
)
from seedcode.tools.subagent import (
    ToolError,
    clear_delegate,
    register_delegate,
)


def _perm(workspace: Path) -> PermissionManager:
    return PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)


class TestSubagentToolContract:
    def test_registered_with_schema(self) -> None:
        tool = get_tool("subagent")
        assert tool.mutates is True  # delegates are never treated as parallel-safe
        assert "task" in tool.args
        assert "agent" in tool.args

    def test_missing_task_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ToolError):
            get_tool("subagent").run(_perm(tmp_path), {})

    def test_no_attached_runner_is_an_honest_tool_error(self, tmp_path: Path) -> None:
        clear_delegate()
        try:
            with pytest.raises(ToolError, match="No sub-agent runner"):
                get_tool("subagent").run(_perm(tmp_path), {"task": "do it"})
        finally:
            clear_delegate()

    def test_delegate_receives_task_and_worker_id(self, tmp_path: Path) -> None:
        seen: list[tuple[str, str]] = []

        def delegate(task: str, worker: str) -> str:
            seen.append((task, worker))
            return "report body"

        register_delegate(delegate)
        try:
            result = get_tool("subagent").run(
                _perm(tmp_path), {"task": "investigate auth", "agent": "explore"}
            )
        finally:
            clear_delegate()
        assert seen == [("investigate auth", "explore")]
        assert result.ok
        assert "report body" in result.output

    def test_runner_crash_is_isolated_to_a_tool_error(self, tmp_path: Path) -> None:
        def broken(task: str, worker: str) -> str:
            raise RuntimeError("child exploded")

        register_delegate(broken)
        try:
            with pytest.raises(ToolError, match="child exploded"):
                get_tool("subagent").run(_perm(tmp_path), {"task": "investigate"})
        finally:
            clear_delegate()

    def test_empty_report_is_reported_as_a_failure(self, tmp_path: Path) -> None:
        register_delegate(lambda task, worker: "   ")
        try:
            with pytest.raises(ToolError, match="empty report"):
                get_tool("subagent").run(_perm(tmp_path), {"task": "investigate"})
        finally:
            clear_delegate()


def _engine(tmp_path: Path) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "test-key")
    perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
    return AgentEngine(config, perm)


class TestEngineDelegation:
    def test_main_engine_registers_itself_as_the_delegate(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        clear_delegate()
        engine = _engine(tmp_path)
        from seedcode.tools.subagent import _delegate  # wiring state

        try:
            assert _delegate is not None
        finally:
            clear_delegate()

    def test_depth_guard_refuses_nested_delegation(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.subagent_depth = 1
        with pytest.raises(ToolError, match="one level deep"):
            engine._spawn_subagent("anything")

    def test_child_engine_runs_isolated(self, tmp_path: Path, monkeypatch) -> None:
        engine = _engine(tmp_path)
        captured: dict = {}

        def fake_run_turn(self, text: str) -> str:
            captured["prompt"] = text
            captured["depth"] = self.subagent_depth
            captured["agent"] = self._agent_id
            captured["same_permissions"] = self.permissions is engine.permissions
            captured["fresh_history"] = len(self.messages) <= 1  # system only
            captured["config_model"] = self.config.model
            captured["steps"] = self.max_steps
            return "the child did the work"

        monkeypatch.setattr(AgentEngine, "run_turn", fake_run_turn)
        try:
            report = engine._spawn_subagent("map the auth flow", "explore")
        finally:
            clear_delegate()
        assert report == "the child did the work"
        assert captured["prompt"] == "map the auth flow"
        assert captured["agent"] == "explore"
        assert captured["depth"] == 1
        assert captured["same_permissions"] is True  # never grants duties
        assert captured["fresh_history"] is True
        assert captured["config_model"] == "test/model"  # no profile override
        assert captured["steps"] == 10  # explore profile budget

    def test_profile_model_override_applies_to_the_child_only(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        engine = _engine(tmp_path)
        engine._registry._overrides["worker"] = AgentProfile(
            id="worker",
            name="Worker",
            description="test",
            system="work",
            steps=5,
            provider="openrouter",
            model="worker/model-9",
        )
        captured: dict = {}
        session_model = engine.config.model

        def fake_run_turn(self, text: str) -> str:
            captured["model"] = self.config.model
            captured["steps"] = self.max_steps
            return "done"

        monkeypatch.setattr(AgentEngine, "run_turn", fake_run_turn)
        try:
            engine._spawn_subagent("build stub", "worker")
        finally:
            clear_delegate()
        assert captured["model"] == "worker/model-9"
        assert captured["steps"] == 5
        # the SESSION routing is untouched
        assert engine.config.model == session_model == "test/model"

    def test_child_failure_becomes_a_parent_tool_error(self, tmp_path: Path, monkeypatch) -> None:
        engine = _engine(tmp_path)

        def failing(self, text: str) -> str:
            raise RuntimeError("provider exploded mid-child")

        monkeypatch.setattr(AgentEngine, "run_turn", failing)
        try:
            with pytest.raises(ToolError, match="provider exploded"):
                engine._spawn_subagent("run something", "explore")
            # the delegate registration is restored so the session delegates again
            from seedcode.tools.subagent import _delegate

            assert _delegate == engine._spawn_subagent
        finally:
            clear_delegate()


class TestProfileModelRouting:
    def test_agents_json_parses_provider_and_model(self, tmp_path: Path) -> None:
        from seedcode.core.agents import AgentRegistry, _parse_agents_file

        path = tmp_path / "agents.json"
        path.write_text(
            '{"worker": {"name": "Worker", "system": "work", '
            '"provider": "anthropic", "model": "claude-sonnet-4"}}',
            encoding="utf-8",
        )
        profiles = _parse_agents_file(path, "project")
        profile = profiles["worker"]
        assert profile.provider == "anthropic"
        assert profile.model == "claude-sonnet-4"

    def test_blank_routing_is_none_not_empty_strings(self, tmp_path: Path) -> None:
        from seedcode.core.agents import _parse_agents_file

        path = tmp_path / "agents.json"
        path.write_text(
            '{"w": {"name": "W", "system": "work", "provider": "", "model": ""}}',
            encoding="utf-8",
        )
        profile = _parse_agents_file(path, "project")["w"]
        assert profile.provider is None
        assert profile.model is None

    def test_describe_mentions_routing(self) -> None:
        profile = AgentProfile(
            id="x", name="X", description="d", provider="p1", model="m1"
        )
        line = describe(profile)
        assert "model p1 / m1" in line

    def test_delegation_records_the_childs_call_state(self, tmp_path: Path, monkeypatch) -> None:
        """The parent's tracker shows the subagent call as a real completed call."""
        engine = _engine(tmp_path)
        monkeypatch.setattr(AgentEngine, "run_turn", lambda self, text: "report")
        try:
            results, failed = engine._execute_calls(
                [ToolCall("subagent", {"task": "go"}, call_id="sa_1")]
            )
        finally:
            clear_delegate()
        assert not failed, results
        state = engine.calls.records["sa_1"]
        assert state.status == STATUS_COMPLETED
        assert "report" in state.output
