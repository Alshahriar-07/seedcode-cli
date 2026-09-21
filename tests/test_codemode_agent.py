"""Phase 5: the Code Mode agent loop end-to-end (file + terminal tools).

Drives a real :class:`AgentEngine` with scripted model replies over the text
protocol, so file creation, editing, and command execution are exercised
through the actual tool registry and permission gate — no network, and no
mocking of the tools themselves.

The permission gate is real: a write outside the workspace must still be
blocked, proving the agent cannot escape the workspace just because it is
"in code mode".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from seedcode import codemode_state as cms
from seedcode.core.agent import AgentEngine
from seedcode.core.models import AppConfig
from seedcode.tools import PermissionLevel, PermissionManager


@pytest.fixture(autouse=True)
def _clean_codemode():
    cms.reset()
    yield
    cms.reset()


def _block(tool: str, args: dict) -> str:
    """One text-protocol tool call block, exactly as the loop parses it."""
    return "```tool\n" + json.dumps({"tool": tool, "args": args}) + "\n```"


def _make_engine(
    workspace: Path, replies: list[str], events: list[tuple[str, str]]
) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)
    engine = AgentEngine(config, perm, on_event=lambda k, d: events.append((k, d)))
    engine._native = False  # force the text protocol (no provider calls)
    queue = iter(replies)

    def _reply():
        try:
            return iter([next(queue)])
        except StopIteration:
            return iter([""])

    engine.stream_reply = _reply  # type: ignore[method-assign]
    return engine


def test_agent_creates_file_then_runs_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cms.enable(tmp_path)

    content = "def hello():\n    return 'Hello Seed Code'\n\nprint(hello())\n"
    events: list[tuple[str, str]] = []
    engine = _make_engine(
        tmp_path,
        [
            _block("write_file", {"path": "hello.py", "content": content}),
            _block("run_command", {"command": f'"{sys.executable}" hello.py'}),
            "Created hello.py and ran it.",
        ],
        events,
    )

    final = engine.run_turn("Create hello.py with hello() and run it.")

    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == content
    # The terminal tool really ran and its output reached the model.
    assert any(k == "result" and "Hello Seed Code" in d for k, d in events)
    assert final.strip()


def test_agent_edits_existing_file_and_verifies(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cms.enable(tmp_path)

    original = 'def hello():\n    return "Hello Seed Code"\n\nprint(hello())\n'
    (tmp_path / "hello.py").write_text(original, encoding="utf-8")

    events: list[tuple[str, str]] = []
    engine = _make_engine(
        tmp_path,
        [
            _block("read_file", {"path": "hello.py"}),
            _block(
                "edit_file",
                {
                    "path": "hello.py",
                    "old_text": '"Hello Seed Code"',
                    "new_text": '"Hello from Seed Code v6.2.5"',
                },
            ),
            _block("run_command", {"command": f'"{sys.executable}" hello.py'}),
            "Updated and re-ran hello.py.",
        ],
        events,
    )

    engine.run_turn("Change hello.py to return 'Hello from Seed Code v6.2.5' and run it.")

    updated = (tmp_path / "hello.py").read_text(encoding="utf-8")
    assert updated.count("Hello from Seed Code v6.2.5") == 1
    assert '"Hello Seed Code"' not in updated
    assert any(
        k == "result" and "Hello from Seed Code v6.2.5" in d for k, d in events
    )


def test_agent_cannot_write_outside_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cms.enable(tmp_path)

    events: list[tuple[str, str]] = []
    engine = _make_engine(
        tmp_path,
        [
            _block("write_file", {"path": "../escape.txt", "content": "nope"}),
            "I was blocked by the workspace boundary.",
        ],
        events,
    )

    engine.run_turn("Write a file outside the workspace.")

    assert not (tmp_path.parent / "escape.txt").exists()
    assert any(k == "error" for k, _ in events)


def test_codemode_prompt_carries_workflow_and_terminal(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cms.enable(tmp_path)

    engine = _make_engine(tmp_path, ["done"], [])
    prompt = engine.messages[0].content

    assert "CODE MODE is ON" in prompt  # the coding-agent workflow instruction
    assert "TERMINAL:" in prompt  # shell/host awareness
    assert str(tmp_path.resolve()) in prompt  # real workspace path
