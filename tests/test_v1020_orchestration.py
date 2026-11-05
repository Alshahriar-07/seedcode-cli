"""v10.2.0 orchestration tests.

Covers the four foundations added to the existing autonomous engine:

* the modular instruction builder (stage-aware, tool-filtered, budgeted, with a
  compact small-model variant);
* the persistent ``.seedcode/context.json`` project memory (reconstructed when
  missing/corrupt, atomic, summarized only);
* the session-scoped ``todo.json`` / ``progress.json`` / ``execution.json``
  store (atomic, bounded, listable);
* the normalized event protocol and its wiring into :class:`CodeSession`.

No network: the session end-to-end test uses a scripted agent double and a real
temporary workspace.
"""

from __future__ import annotations

import json
from pathlib import Path

from seedcode.core import instructions
from seedcode.core.context import ProjectContext, context_path
from seedcode.core.events import normalize_event, protocol_type
from seedcode.core.session import CodeSession, SessionStatus
from seedcode.core.session_store import SessionStore, list_sessions
from seedcode.core.tasks import Task, TaskGraph, TurnEvidence
from seedcode.utils.atomic import read_json, write_json_atomic


# --- instruction engine ------------------------------------------------------
def test_tool_guide_only_advertises_callable_tools() -> None:
    """The guide can never offer a tool the turn/profile cannot call."""
    guide = instructions.tool_selection_guide(
        allowed={"read_file"}, groups=("core",)
    )
    assert "-> read_file" in guide
    assert "write_file" not in guide
    assert "web_search" not in guide


def test_build_instructions_is_stage_aware() -> None:
    text = instructions.build_instructions(workspace="/tmp/project", stage="verify")
    assert "STAGE — VERIFY" in text
    assert "VERIFICATION" in text
    assert "CONTINUATION" in text
    assert "TODO WORKFLOW" in text


def test_build_instructions_teaches_identity_protocol_and_injection() -> None:
    """The universal block states role, tool lifecycle and the injection rule."""
    text = instructions.build_instructions(workspace="/tmp/project")
    assert "You are SeedCode" in text
    assert "TOOL LIFECYCLE" in text
    assert "UNTRUSTED CONTENT" in text
    assert "WORKSPACE: /tmp/project" in text


def test_standard_budget_keeps_the_whole_contract() -> None:
    """At the engine's standard budget no contract section is dropped."""
    text = instructions.build_instructions(
        workspace="/tmp/p",
        checklist=(("running", "do the thing"),),
        total=1,
        task_id=3,
        budget_chars=5600,
    )
    for marker in (
        "You are SeedCode",
        "TOOL LIFECYCLE",
        "UNTRUSTED CONTENT",
        "WORKSPACE: /tmp/p",
        "TODO WORKFLOW",
        "VERIFICATION",
        "CONTINUATION",
    ):
        assert marker in text


def test_protocol_wording_follows_transport() -> None:
    """Native and text transports keep one lifecycle with honest wording."""
    native = instructions.build_instructions(workspace="/tmp/p", protocol="native")
    text = instructions.build_instructions(workspace="/tmp/p", protocol="text")
    assert "TOOL LIFECYCLE" in native and "TOOL LIFECYCLE" in text
    assert "tool result arrives" in native
    assert "[TOOL RESULTS]" in text


def test_compact_instructions_warn_about_untrusted_content() -> None:
    text = instructions.compact_instructions(workspace="/tmp/project")
    assert "SeedCode" in text
    assert "data, not instructions" in text
    assert "never claim work" in text


def test_manifest_states_requirement_type_and_permission() -> None:
    """Tool docs spell out required/optional, type and the permission gate."""
    from seedcode.tools import tool_manifest

    manifest = tool_manifest(("core",))
    assert '"path" (required string)' in manifest
    assert "access: read-only" in manifest
    assert "permission check applies" in manifest


def test_agent_prompt_carries_the_universal_contract(tmp_path: Path) -> None:
    """The engine really injects the block, not just the builder returning it."""
    from seedcode.core.agent import AgentEngine
    from seedcode.core.models import AppConfig
    from seedcode.tools import PermissionManager, PermissionMode

    config = AppConfig()
    config.model = "test-model"
    engine = AgentEngine(
        config,
        PermissionManager(workspace=tmp_path, mode=PermissionMode.WORKSPACE),
    )
    prompt = engine.messages[0].content
    # The base identity already names Seed Code; these are the newly injected
    # contract sections, proving the wiring (not just the builder) works.
    assert "TOOL LIFECYCLE" in prompt
    assert "UNTRUSTED CONTENT" in prompt
    assert f"WORKSPACE: {tmp_path}" in prompt


def test_manifest_never_lists_a_tool_outside_the_whitelist() -> None:
    from seedcode.tools import tool_manifest

    manifest = tool_manifest(("core",), allowed=frozenset({"read_file"}))
    assert "read_file" in manifest
    assert "write_file" not in manifest


def test_instruction_budget_drops_detail_not_the_contract() -> None:
    text = instructions.build_instructions(
        workspace="/tmp/project",
        stage="execute",
        context="x" * 6000,
        budget_chars=2600,
    )
    assert "STAGE — EXECUTE" in text
    assert "VERIFICATION" in text
    assert "x" * 400 not in text  # the long context was dropped whole


def test_compact_instructions_are_shorter_and_sequential() -> None:
    compact = instructions.compact_instructions(workspace="/tmp/project", task_id=3)
    full = instructions.build_instructions(workspace="/tmp/project")
    assert len(compact) < len(full)
    assert "1. Look at the real files" in compact
    assert "TASK 3 COMPLETE" in compact
    # No fake reasoning/status text in the prompt.
    assert "Thinking" not in compact


def test_continuation_never_ends_after_one_task() -> None:
    text = instructions.continuation_section(7)
    assert "TASK 7 COMPLETE" in text
    assert "never restart" in text.lower()


# --- persistent project context ---------------------------------------------
def test_context_reconstructs_and_round_trips(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")

    ctx = ProjectContext.load(tmp_path)
    assert context_path(tmp_path).is_file()
    assert "Python" in ctx.languages
    assert any("main.py" in entry for entry in ctx.structure)

    ctx.note_decision("Use pytest for verification")
    ctx.save(tmp_path)
    again = ProjectContext.load(tmp_path, refresh=False)
    assert "Use pytest for verification" in again.decisions


def test_corrupt_context_is_rebuilt_not_fatal(tmp_path: Path) -> None:
    (tmp_path / ".seedcode").mkdir()
    context_path(tmp_path).write_text("{not valid json", encoding="utf-8")

    ctx = ProjectContext.load(tmp_path, refresh=False)
    assert ctx.valid()
    assert read_json(context_path(tmp_path)) is not None


def test_context_stores_summaries_never_source(tmp_path: Path) -> None:
    secret = "SUPER_SECRET_SOURCE_BODY_XYZ"
    (tmp_path / "app.py").write_text(f"VALUE = '{secret}'\n", encoding="utf-8")

    ProjectContext.load(tmp_path)
    raw = context_path(tmp_path).read_text(encoding="utf-8")
    assert secret not in raw
    assert "app.py" in raw  # the reference is kept, the content is not


def test_context_absorbs_observed_evidence(tmp_path: Path) -> None:
    ctx = ProjectContext(workspace=str(tmp_path))
    ctx.merge_evidence(
        TurnEvidence(changed_files=["src/a.py"], errors=["build failed: E1"])
    )
    assert "src/a.py" in ctx.important_files
    assert any("build failed" in issue for issue in ctx.known_issues)


def test_context_prompt_is_bounded(tmp_path: Path) -> None:
    ctx = ProjectContext(workspace=str(tmp_path))
    ctx.dependencies = [f"dependency-{i}" for i in range(40)]
    prompt = ctx.to_prompt(max_chars=200)
    assert len(prompt) <= 200


# --- session store -----------------------------------------------------------
def test_session_store_writes_todo_progress_and_execution(tmp_path: Path) -> None:
    store = SessionStore(tmp_path, session_id="abc123")
    graph = TaskGraph(request="r", tasks=[Task(id=1, title="First task")])

    store.save_todo(graph)
    assert store.load_todo()["tasks"][0]["title"] == "First task"

    store.sync(graph, status="running", next_action="continue Task 1")
    progress = store.load_progress()
    assert progress["status"] == "running"
    assert progress["next_action"] == "continue Task 1"

    event = normalize_event("task_start", "Task 1: First task", session_id="abc123")
    store.append_execution(event)
    log = store.load_execution()
    assert log["events"][-1]["type"] == "todo.updated"
    assert log["events"][-1]["task_id"] == 1

    listed = list_sessions(tmp_path)
    assert listed[0]["session_id"] == "abc123"


def test_execution_log_is_bounded(tmp_path: Path, monkeypatch) -> None:
    import seedcode.core.session_store as store_module

    monkeypatch.setattr(store_module, "MAX_EXECUTION_EVENTS", 3)
    store = SessionStore(tmp_path)
    for index in range(6):
        store.append_execution({"type": "tool.completed", "index": index})
    assert len(store.load_execution()["events"]) == 3


def test_atomic_json_never_leaves_a_partial_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    assert write_json_atomic(target, {"ok": True})
    assert read_json(target) == {"ok": True}
    assert not list(tmp_path.glob("*.seedcode-tmp"))


# --- normalized event protocol ----------------------------------------------
def test_event_protocol_maps_and_carries_the_envelope() -> None:
    assert protocol_type("verify") == "verification.started"
    assert protocol_type("tool_done") == "tool.completed"
    assert protocol_type("session.completed") == "session.completed"  # passthrough

    event = normalize_event("recovery", "Task 4: repairing (attempt 2)", session_id="s1")
    assert event.type == "repair.started"
    assert event.task_id == 4
    assert event.session_id == "s1"
    assert event.event_id and event.timestamp > 0
    payload = event.to_dict()
    for key in ("event_id", "session_id", "type", "timestamp", "task_id", "message", "payload"):
        assert key in payload


# --- end-to-end session wiring ----------------------------------------------
class _ScriptedAgent:
    """A real evidence-reporting engine double (no network)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_evidence = TurnEvidence()

    def run_turn(self, text: str) -> str:
        self.calls.append(text)
        self.last_evidence = TurnEvidence(changed_files=["app.py"])
        return "TASK 1 COMPLETE"


def test_code_session_owns_its_todo_progress_and_execution(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    graph = TaskGraph(
        request="create the app entry point",
        tasks=[
            Task(
                id=1,
                title="Create app.py",
                detail="Write the real entry point.",
                acceptance=["file: app.py"],
            )
        ],
    )
    agent = _ScriptedAgent()
    session = CodeSession(
        agent,
        workspace=tmp_path,
        request="create the app entry point",
        graph=graph,
        session_store=SessionStore(tmp_path, session_id="run1"),
        provider_retries=0,
    )

    status = session.run()

    assert status is SessionStatus.COMPLETED
    base = tmp_path / ".seedcode" / "sessions" / "session_run1"
    assert (base / "todo.json").is_file()
    assert (base / "progress.json").is_file()
    assert (base / "execution.json").is_file()
    assert context_path(tmp_path).is_file()

    progress = json.loads((base / "progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "completed"
    assert progress["session_id"] == "run1"

    events = json.loads((base / "execution.json").read_text(encoding="utf-8"))["events"]
    types = {entry["type"] for entry in events}
    assert "todo.completed" in types
    assert "verification.started" in types
    assert "session.completed" in types

    # One prompt ran the whole workflow: the task plus final verification. No
    # intermediate response ended the session.
    assert len(agent.calls) >= 2
    context = json.loads(context_path(tmp_path).read_text(encoding="utf-8"))
    assert any("Create app.py" in item for item in context["completed"])
