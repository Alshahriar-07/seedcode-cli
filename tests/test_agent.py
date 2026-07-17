"""Tests for the agent loop: detection, execution, validation, retry, finish."""

from __future__ import annotations

from pathlib import Path

import pytest

from seedcode.core.agent import AgentEngine, parse_tool_calls, strip_tool_blocks
from seedcode.core.models import AppConfig
from seedcode.tools import PermissionManager, PermissionMode


def tool_block(payload: str) -> str:
    return f"```tool\n{payload}\n```"


# --- parsing -----------------------------------------------------------------
class TestParsing:
    def test_no_blocks(self):
        assert parse_tool_calls("Just a normal answer.") == []

    def test_single_call(self):
        calls = parse_tool_calls(tool_block('{"tool": "read_file", "args": {"path": "a.py"}}'))
        assert len(calls) == 1
        assert calls[0].tool == "read_file" and calls[0].args == {"path": "a.py"} \
            and not calls[0].error

    def test_multiple_calls(self):
        text = (
            tool_block('{"tool": "list_dir", "args": {}}')
            + "\nthen\n"
            + tool_block('{"tool": "read_file", "args": {"path": "b.py"}}')
        )
        assert [c.tool for c in parse_tool_calls(text)] == ["list_dir", "read_file"]

    def test_malformed_json_reports_error(self):
        calls = parse_tool_calls(tool_block('{"tool": "read_file",'))
        assert len(calls) == 1 and calls[0].error

    def test_missing_tool_name_reports_error(self):
        calls = parse_tool_calls(tool_block('{"args": {}}'))
        assert calls[0].error

    def test_strip_tool_blocks(self):
        text = "Before.\n" + tool_block('{"tool": "list_dir", "args": {}}') + "\nAfter."
        stripped = strip_tool_blocks(text)
        assert "Before." in stripped and "After." in stripped and "```tool" not in stripped


# --- the loop -----------------------------------------------------------------
class ScriptedAgent(AgentEngine):
    """AgentEngine whose 'model' is a scripted list of replies (no network)."""

    def __init__(self, workspace: Path, replies: list[str], mode=PermissionMode.WORKSPACE):
        config = AppConfig()
        config.model = "test-model"
        super().__init__(config, PermissionManager(workspace=workspace, mode=mode))
        self._replies = list(replies)
        self.requests = 0

    def stream_reply(self):
        self.requests += 1
        if not self._replies:
            pytest.fail("agent asked for more replies than scripted")
        yield self._replies.pop(0)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hello world\n", encoding="utf-8")
    return tmp_path


class TestAgentLoop:
    def test_plain_answer_ends_turn(self, workspace):
        agent = ScriptedAgent(workspace, ["All done, no tools needed."])
        assert agent.run_turn("hi") == "All done, no tools needed."
        assert agent.requests == 1

    def test_tool_call_then_answer(self, workspace):
        agent = ScriptedAgent(
            workspace,
            [
                tool_block('{"tool": "read_file", "args": {"path": "hello.txt"}}'),
                "The file says hello world.",
            ],
        )
        final = agent.run_turn("what does hello.txt say?")
        assert "hello world" in final
        assert agent.requests == 2
        # The tool result was fed back as a user-role message.
        feedback = [m for m in agent.messages if m.content.startswith("[TOOL RESULTS]")]
        assert feedback and "hello world" in feedback[0].content

    def test_tool_writes_file(self, workspace):
        agent = ScriptedAgent(
            workspace,
            [
                tool_block('{"tool": "write_file", "args": {"path": "out.txt", "content": "made"}}'),
                "Created out.txt.",
            ],
        )
        agent.run_turn("create out.txt")
        assert (workspace / "out.txt").read_text(encoding="utf-8") == "made"

    def test_malformed_call_is_retried(self, workspace):
        agent = ScriptedAgent(
            workspace,
            [
                tool_block('{"tool": "read_file"'),  # broken JSON -> error fed back
                tool_block('{"tool": "read_file", "args": {"path": "hello.txt"}}'),
                "Recovered.",
            ],
        )
        assert agent.run_turn("read it") == "Recovered."
        assert agent.requests == 3

    def test_permission_denial_reaches_model(self, workspace):
        agent = ScriptedAgent(
            workspace,
            [
                tool_block('{"tool": "write_file", "args": {"path": "x.txt", "content": "no"}}'),
                "I could not write the file (read-only).",
            ],
            mode=PermissionMode.READ_ONLY,
        )
        agent.run_turn("write x.txt")
        assert not (workspace / "x.txt").exists()
        feedback = [m for m in agent.messages if m.content.startswith("[TOOL RESULTS]")]
        assert feedback and "Read Only" in feedback[0].content

    def test_step_budget_bounds_the_loop(self, workspace):
        from seedcode.core import agent as agent_mod

        endless = [tool_block('{"tool": "list_dir", "args": {}}')] * (agent_mod.MAX_STEPS + 1)
        agent = ScriptedAgent(workspace, endless)
        final = agent.run_turn("loop forever")
        assert "step limit" in final
        assert agent.requests == agent_mod.MAX_STEPS

    def test_system_prompt_lists_tools_and_mode(self, workspace):
        agent = ScriptedAgent(workspace, ["ok"])
        prompt = agent.messages[0].content
        assert "read_file" in prompt and "run_command" in prompt
        assert "Workspace" in prompt and str(workspace) in prompt
