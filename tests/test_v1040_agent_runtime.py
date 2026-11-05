"""v10.4.0 agent runtime: skills, durable task state and loop guards.

The engine's loop, tool feedback and native/text protocols already existed;
these tests pin the additions that make it reliable with small local models:
progressive-disclosure skills, a durable task record, no-progress detection,
and a bounded correction when the model describes an edit instead of making it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seedcode.core.agent import AgentEngine, _USE_TOOLS_PROMPT
from seedcode.core.events import normalize_event, protocol_type
from seedcode.core.intent import Intent, IntentKind
from seedcode.core.models import AppConfig
from seedcode.core.providers.base import TextDelta, ToolCallEvent
from seedcode.core.skills import SkillError, discover_skills, load_skill, skill_catalog
from seedcode.core.task_state import TaskStateStore, normalize_steps
from seedcode.tools import (
    PermissionLevel,
    PermissionManager,
    get_tool,
)


def _perm(workspace: Path) -> PermissionManager:
    return PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)


def _engine(workspace: Path, events: list | None = None, max_steps: int = 6) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    captured = events if events is not None else []
    return AgentEngine(
        config,
        _perm(workspace),
        on_event=lambda kind, detail: captured.append((kind, detail)),
        max_steps=max_steps,
    )


def _skill(root: Path, name: str, description: str, body: str) -> None:
    folder = root / ".seedcode" / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# skills: discovery, progressive disclosure, safe loading
# ---------------------------------------------------------------------------
def test_skill_catalog_lists_descriptions_without_loading_bodies(tmp_path: Path) -> None:
    _skill(tmp_path, "python-development", "Typed Python and pytest.", "PROJECT BODY")
    home = tmp_path / "home"
    _skill(home, "debugging", "Reproduce before fixing.", "GLOBAL BODY")

    catalog = skill_catalog(tmp_path, home=home)
    assert "python-development" in catalog
    assert "debugging" in catalog
    assert "PROJECT BODY" not in catalog
    assert "GLOBAL BODY" not in catalog

    body = load_skill(tmp_path, "python-development", home=home)
    assert "PROJECT BODY" in body and "---" not in body.splitlines()[0]


def test_project_skill_overrides_global_and_unknown_names_are_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _skill(home, "testing", "Global testing.", "GLOBAL VERSION")
    _skill(tmp_path, "testing", "Project testing.", "PROJECT VERSION")

    discovered = discover_skills(tmp_path, home=home)
    assert discovered["testing"].source == "project"
    assert "PROJECT VERSION" in load_skill(tmp_path, "testing", home=home)

    with pytest.raises(SkillError):
        load_skill(tmp_path, "../../etc/passwd", home=home)


def test_use_skill_tool_loads_instructions_and_reports_unknown_names(tmp_path: Path) -> None:
    _skill(tmp_path, "git-workflow", "Commit hygiene.", "FORMAT THE MESSAGE")
    result = get_tool("use_skill").run(_perm(tmp_path), {"name": "git-workflow"})
    assert result.ok and "FORMAT THE MESSAGE" in result.output

    missing = get_tool("use_skill").run(_perm(tmp_path), {"name": "nope"})
    assert not missing.ok and "Available skills" in missing.output


def test_skill_catalog_is_injected_into_the_system_prompt(tmp_path: Path) -> None:
    _skill(tmp_path, "python-development", "Typed Python.", "BODY")
    engine = _engine(tmp_path)
    prompt = engine._system_prompt()
    assert "AVAILABLE SKILLS" in prompt
    assert "python-development" in prompt
    assert "BODY" not in prompt.split("AVAILABLE SKILLS", 1)[1]


# ---------------------------------------------------------------------------
# durable task state
# ---------------------------------------------------------------------------
def test_task_state_round_trip_and_tool_persist_to_the_project(tmp_path: Path) -> None:
    assert normalize_steps("Inspect\nImplement\nTest") == [
        {"id": 1, "description": "Inspect", "status": "pending", "detail": ""},
        {"id": 2, "description": "Implement", "status": "pending", "detail": ""},
        {"id": 3, "description": "Test", "status": "pending", "detail": ""},
    ]

    store = TaskStateStore(tmp_path)
    state = store.update_from_tool(
        {
            "task": "Build authentication",
            "steps": [{"description": "Inspect project"}, {"description": "Implement"}],
        }
    )
    assert store.save(state)
    loaded = store.load()
    assert loaded is not None and loaded.task == "Build authentication"
    assert loaded.progress() == (0, 2)

    result = get_tool("update_task").run(
        _perm(tmp_path),
        {"current_step": 1, "last_result": "inspected the project"},
    )
    assert result.ok, result.output
    data = json.loads((tmp_path / ".seedcode" / "todo.json").read_text(encoding="utf-8"))
    assert data["current_step"] == 1
    assert data["steps"][0]["status"] == "in_progress"
    assert data["last_result"] == "inspected the project"


def test_task_state_is_injected_into_the_system_prompt(tmp_path: Path) -> None:
    store = TaskStateStore(tmp_path)
    store.save(
        store.update_from_tool(
            {"task": "Ship feature", "steps": ["Inspect", "Implement", "Verify"]}
        )
    )
    engine = _engine(tmp_path)
    prompt = engine._system_prompt()
    assert "CURRENT TASK STATE" in prompt
    assert "Ship feature" in prompt
    assert "[ ] 2. Implement" in prompt


# ---------------------------------------------------------------------------
# the loop: real tool results are fed back and the model continues
# ---------------------------------------------------------------------------
def test_loop_executes_a_tool_then_continues_with_its_real_result(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    events: list[tuple[str, str]] = []
    engine = _engine(tmp_path, events)
    engine._native = True
    calls = {"n": 0}

    def _stream(specs):
        calls["n"] += 1
        if calls["n"] == 1:
            yield ToolCallEvent(id="call-1", name="list_dir", arguments={"path": "."})
        else:
            yield TextDelta("Finished inspecting main.py.")
        yield from ()

    engine.stream_reply_events = _stream  # type: ignore[method-assign]

    reply = engine.run_turn("Inspect the project files and report")

    assert reply == "Finished inspecting main.py."
    assert calls["n"] == 2
    tool_messages = [m for m in engine.messages if m.role == "tool"]
    assert tool_messages and tool_messages[0].tool_name == "list_dir"
    assert "main.py" in tool_messages[0].content
    assert any(kind == "iteration" for kind, _detail in events)
    assert any(kind == "agent_started" for kind, _detail in events)
    assert any(kind == "agent_completed" for kind, _detail in events)


def test_identical_steps_are_detected_as_no_progress(tmp_path: Path) -> None:
    engine = _engine(tmp_path, max_steps=8)
    engine._native = True

    def _stream(specs):
        yield ToolCallEvent(id="same-call", name="list_dir", arguments={"path": "."})
        yield from ()

    engine.stream_reply_events = _stream  # type: ignore[method-assign]
    reply = engine.run_turn("Inspect the workspace repeatedly")

    assert "No progress" in reply
    assert engine.last_evidence.incomplete is True


def test_code_in_prose_for_a_change_request_is_nudged_to_apply_it(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    engine.set_intent(Intent(kind=IntentKind.CODING, needs_workspace=True))
    engine._user_request = "Build a parser module for the config files"
    code = "```python\nprint('a')\nprint('b')\nprint('c')\n```"

    outcome, payload = engine._finish(code, "")
    assert outcome == "ok" and payload is None
    assert engine.messages[-1].role == "user"
    assert engine.messages[-1].content == _USE_TOOLS_PROMPT

    # Bounded: the second prose answer is accepted, never looped forever.
    outcome, payload = engine._finish("I cannot apply it here.", "")
    assert outcome == "final" and payload == "I cannot apply it here."


def test_a_claim_of_file_work_without_tools_is_nudged_even_without_code(
    tmp_path: Path,
) -> None:
    """The real qwen3:4b failure: claimed creation, empty workspace, no fence."""
    engine = _engine(tmp_path)
    engine.set_intent(Intent(kind=IntentKind.CODING, needs_workspace=True))
    engine._user_request = "Create hello.py and run it, then report the output"
    outcome, payload = engine._finish(
        "I created hello.py and executed it. The real output is seed-ok.", ""
    )
    assert outcome == "ok" and payload is None
    assert engine.messages[-1].content == _USE_TOOLS_PROMPT
    assert "```tool" in engine.messages[-1].content


def test_a_clarifying_question_is_not_pushed_into_tool_use(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine.set_intent(Intent(kind=IntentKind.CODING, needs_workspace=True))
    engine._user_request = "Create hello.py and run it"
    outcome, payload = engine._finish("Which language should hello.py use?", "")
    assert outcome == "final" and payload == "Which language should hello.py use?"


def test_ignored_correction_never_presents_prose_as_applied_work(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    engine.set_intent(Intent(kind=IntentKind.CODING, needs_workspace=True))
    engine._user_request = "Create hello.py and run it"
    prose = "I created hello.py. All done."

    first, _ = engine._finish(prose, "")
    assert first == "ok"
    second, final = engine._finish(prose, "")
    assert second == "final"
    assert final is not None
    assert final.startswith("[No workspace changes were applied")
    assert prose in final
    assert not (tmp_path / "hello.py").exists()


def test_show_me_code_requests_are_not_nudged(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine.set_intent(Intent(kind=IntentKind.CODING, needs_workspace=True))
    engine._user_request = "Show me an example of a parser without editing files"
    code = "```python\nprint('a')\nprint('b')\nprint('c')\n```"
    outcome, payload = engine._finish(code, "")
    assert outcome == "final" and payload == code


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------
def test_agent_protocol_event_names() -> None:
    assert protocol_type("iteration") == "agent.iteration"
    assert normalize_event("thinking", "real reasoning").type == "agent.reasoning"
    assert normalize_event("agent_started", "max_steps=6").type == "agent.started"
    assert normalize_event("agent_completed", "done").type == "agent.completed"
