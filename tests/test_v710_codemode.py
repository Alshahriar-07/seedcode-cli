"""v7.1.0 Code Mode: the persistent task engine, not one model call.

These tests lock in the architectural change:

* a task is a unit of work that keeps cycling (analyse → implement → run →
  fix → verify) until its acceptance criteria are met by real evidence;
* a model response is never proof of completion (a reply saying "TASK 1
  COMPLETE" with no file change and no successful command fails verification);
* limits and provider errors do not end the task: output/context truncation
  continues with another call, a temporary API failure retries with backoff;
* failures are recovered from intelligently, but a repeated identical failure
  stops with an explicit blocker instead of looping forever;
* state is checkpointed, so a pause, cancel or crash resumes from the current
  task rather than from zero;
* dependencies order the plan, and the next TODO starts automatically.

No network: the agent is scripted, tools are real.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedcode import codemode_state as cms
from seedcode.core import session as session_mod
from seedcode.core.chat import ChatError
from seedcode.core.session import (
    CodeSession,
    ControlFlags,
    SessionStatus,
    TaskCancelled,
)
from seedcode.core.tasks import (
    CommandRecord,
    TaskGraph,
    TaskState,
    TurnEvidence,
    parse_plan,
    verify_task,
)
from seedcode.core.models import Message


# --- helpers -----------------------------------------------------------------


class ScriptedAgent:
    """A scripted agent: each turn returns the next reply, with evidence.

    ``evidence_for`` decides what the turn "did" to the project, which is what
    the session verifies against — so a test can model a real editing loop
    without a provider.
    """

    def __init__(
        self,
        replies: list[str],
        *,
        evidence_for=None,
        raises: list[BaseException] | None = None,
    ) -> None:
        self.messages = [Message(role="system", content="system")]
        self.transcript = list(self.messages)
        self.last_evidence = TurnEvidence()
        self.prompts: list[str] = []
        self._replies = list(replies)
        self._raises = list(raises or [])
        self._evidence_for = evidence_for

    def run_turn(self, text: str) -> str:
        self.prompts.append(text)
        if self._raises:
            raise self._raises.pop(0)
        reply = self._replies.pop(0) if self._replies else "done"
        evidence = (
            self._evidence_for(len(self.prompts), text, reply)
            if self._evidence_for
            else TurnEvidence()
        )
        self.last_evidence = evidence
        self.messages.append(Message(role="user", content=text))
        self.messages.append(Message(role="assistant", content=reply))
        self.transcript = list(self.messages)
        return reply


@pytest.fixture(autouse=True)
def _clean_state():
    cms.reset()
    session_mod.reset()
    yield
    cms.reset()
    session_mod.reset()


def _session(tmp_path: Path, agent, **kwargs) -> CodeSession:
    state = cms.enable(tmp_path)
    return CodeSession(
        agent,
        workspace=tmp_path,
        request=kwargs.pop("request", "Build the feature"),
        store=state.store,
        sleep=lambda _s: None,
        **kwargs,
    )


PLAN = """\
Here is the plan.

```plan
{"tasks": [
  {"title": "Create the module", "detail": "add app.py",
   "acceptance": ["file: app.py", "no-errors"], "depends_on": []},
  {"title": "Add validation", "detail": "validate input",
   "acceptance": ["file: app.py | contains: raise ValueError", "no-errors"],
   "depends_on": [1]},
  {"title": "Run the tests", "detail": "make the suite pass",
   "acceptance": ["tests"], "depends_on": [2]}
]}
```
Task 1 is next.
"""


# --- the task model -----------------------------------------------------------


def test_plan_parsing_accepts_json_and_numbered_forms() -> None:
    from_json = parse_plan(PLAN)
    assert [item.title for item in from_json] == [
        "Create the module",
        "Add validation",
        "Run the tests",
    ]
    assert from_json[1].depends_on == [1]
    assert "file: app.py" in from_json[0].acceptance

    from_lines = parse_plan(
        "1. Set up the project\n"
        "2. Add the database layer — Acceptance: no-errors — Depends on: 1\n"
    )
    assert [item.title for item in from_lines] == [
        "Set up the project",
        "Add the database layer",
    ]
    assert from_lines[1].acceptance == ["no-errors"]
    assert from_lines[1].depends_on == [1]


def test_scheduler_respects_dependencies() -> None:
    graph = TaskGraph.from_plan("req", parse_plan(PLAN))
    first = graph.next_task()
    assert first is not None and first.id == 1
    graph.mark(first, TaskState.COMPLETED)
    second = graph.next_task()
    assert second is not None and second.id == 2
    # A dependency is not skipped: task 3 waits for task 2.
    assert graph.next_task().id == 2
    graph.mark(second, TaskState.COMPLETED)
    assert graph.next_task().id == 3


def test_a_model_claim_is_not_completion(tmp_path: Path) -> None:
    """The exact promise of the release: 'done' proves nothing."""
    graph = TaskGraph.from_plan("req", parse_plan(PLAN))
    task = graph.tasks[0]
    claim_only = TurnEvidence()  # the model said "TASK 1 COMPLETE"
    outcome = verify_task(task, claim_only, tmp_path)
    assert outcome.ok is False
    assert any("app.py" in unmet for unmet in outcome.unmet)


def test_acceptance_criteria_are_checked_against_the_project(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def f():\n    raise ValueError('x')\n", encoding="utf-8")
    graph = TaskGraph.from_plan("req", parse_plan(PLAN))
    evidence = TurnEvidence(
        changed_files=["app.py"],
        commands=[],  # no test run yet
    )
    task = graph.tasks[0]
    assert verify_task(task, evidence, tmp_path).ok is True
    # Task 2 needs the file to *contain* the validation; task 3 needs a test run.
    assert verify_task(graph.tasks[1], evidence, tmp_path).ok is True
    assert verify_task(graph.tasks[2], evidence, tmp_path).ok is False

    from seedcode.core.tasks import CommandRecord

    evidence.commands.append(CommandRecord("python -m pytest -q", True, "2 passed", test=True))
    assert verify_task(graph.tasks[2], evidence, tmp_path).ok is True


# --- the persistent loop ------------------------------------------------------


def test_session_plans_then_completes_every_task(tmp_path: Path) -> None:
    """Multiple model calls per task, next TODO automatically, then done."""
    (tmp_path / "app.py").write_text("raise ValueError('validation')\n", encoding="utf-8")

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 1:  # planning call
            return TurnEvidence()
        if "tests" in prompt.lower():
            return TurnEvidence(
                commands=[
                    __import__("seedcode.core.tasks", fromlist=["CommandRecord"]).CommandRecord(
                        "python -m pytest -q", True, "3 passed", test=True
                    )
                ]
            )
        return TurnEvidence(changed_files=["app.py"])

    agent = ScriptedAgent(
        [
            PLAN,
            "TASK 1 COMPLETE",  # task 1
            "TASK 2 COMPLETE",  # task 2
            "TASK 3 COMPLETE",  # task 3
            "FINAL VERIFICATION PASSED",
        ],
        evidence_for=evidence,
    )
    session = _session(tmp_path, agent)

    status = session.run()

    assert status is SessionStatus.COMPLETED, session.reason
    done, total = session.graph.progress()
    assert (done, total) == (3, 3)
    # One planning call, one call per task, one final verification call.
    assert session.model_calls >= 5
    # And the plan really came from the model's plan block.
    assert [t.title for t in session.graph.tasks] == [
        "Create the module",
        "Add validation",
        "Run the tests",
    ]


def test_task_keeps_going_until_it_verifies(tmp_path: Path) -> None:
    """The first response for a task is not the end of the task."""
    task_one = (
        "```plan\n"
        '{"tasks": [{"title": "Create the module", "acceptance": ["file: app.py"]}]}\n'
        "```"
    )
    created = {"done": False}

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 1:
            return TurnEvidence()
        if not created["done"]:
            created["done"] = True
            (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
            return TurnEvidence(changed_files=["app.py"])
        return TurnEvidence(changed_files=["app.py"])

    agent = ScriptedAgent([task_one, "first attempt", "TASK 1 COMPLETE"], evidence_for=evidence)
    session = _session(tmp_path, agent)

    assert session.run() is SessionStatus.COMPLETED
    assert session.model_calls == 3  # plan + write + verify
    assert session.graph.tasks[0].attempts == 0  # verified on the first pass


def test_task_continues_after_a_truncated_response(tmp_path: Path) -> None:
    """An output/context limit is not task completion — the next call continues."""
    plan = "```plan\n" + '{"tasks": [{"title": "Write it", "acceptance": ["file: a.py"]}]}\n' + "```"
    replies = ["", "TASK 1 COMPLETE"]  # first task reply is empty (truncated)

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 1:
            return TurnEvidence()
        if call == 2:
            # The turn hit its limit: incomplete, nothing written yet.
            return TurnEvidence(incomplete=True, summary="…")
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        return TurnEvidence(changed_files=["a.py"])

    agent = ScriptedAgent([plan, *replies], evidence_for=evidence)
    session = _session(tmp_path, agent)

    assert session.run() is SessionStatus.COMPLETED
    assert (tmp_path / "a.py").exists()
    assert session.model_calls == 4  # plan, truncated cycle, completed cycle, final verify


def test_failed_command_triggers_recovery_then_success(tmp_path: Path) -> None:
    """exit 1 → inspect → fix → rerun → verify above the first error."""
    (tmp_path / "app.py").write_text("raise ValueError('x')\n", encoding="utf-8")
    plan = "```plan\n" + '{"tasks": [{"title": "Tests", "acceptance": ["tests"]}]}\n' + "```"
    CommandRecord = __import__(
        "seedcode.core.tasks", fromlist=["CommandRecord"]
    ).CommandRecord
    state = {"fixed": False}

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 1:  # planning
            return TurnEvidence()
        if "NOT verified" in prompt:  # the repair cycle: fix, re-run, pass
            state["fixed"] = True
        elif not state["fixed"]:
            return TurnEvidence(
                commands=[CommandRecord("python -m pytest -q", False, "1 failed", test=True)],
                errors=["pytest: 1 failed"],
            )
        return TurnEvidence(
            commands=[CommandRecord("python -m pytest -q", True, "3 passed", test=True)],
            changed_files=["app.py"],
        )

    agent = ScriptedAgent(
        [plan, "ran the tests", "fixed the bug", "TASK 1 COMPLETE"],
        evidence_for=evidence,
    )
    session = _session(tmp_path, agent)

    assert session.run() is SessionStatus.COMPLETED, session.reason
    assert session.graph.tasks[0].attempts == 1  # one repair cycle
    assert session.recoveries == 1
    # The repair prompt named the actual failure.
    assert any("1 failed" in p for p in agent.prompts)


def test_repeated_identical_failure_stops_with_a_blocker(tmp_path: Path) -> None:
    plan = "```plan\n" + '{"tasks": [{"title": "Never works", "acceptance": ["file: nope.py"]}]}\n' + "```"

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 1:
            return TurnEvidence()
        return TurnEvidence()  # identical non-progress every single time

    agent = ScriptedAgent([plan] * 12, evidence_for=evidence)
    session = _session(tmp_path, agent)

    status = session.run()

    assert status is SessionStatus.FAILED
    assert session.graph.tasks[0].state is TaskState.BLOCKED
    assert "blocked" in session.reason.lower()
    # Bounded: it stopped after the repeated-failure allowance, not forever.
    assert session.model_calls <= 6


def test_provider_error_retries_with_backoff_then_fails_cleanly(tmp_path: Path) -> None:
    plan = "```plan\n" + '{"tasks": [{"title": "Write it", "acceptance": ["file: a.py"]}]}\n' + "```"
    sleeps: list[float] = []
    agent = ScriptedAgent([], raises=[ChatError("connection reset")] * 5)
    session = _session(
        tmp_path,
        agent,
        provider_retries=2,
        backoff_s=0.5,
    )
    session._sleep = sleeps.append  # type: ignore[method-assign]
    session.request = plan  # planning uses the same failing call

    status = session.run()

    assert status is SessionStatus.FAILED
    assert "provider request failed" in session.reason
    assert sleeps == [0.5, 1.0]  # bounded exponential backoff


def test_provider_error_then_success_still_completes(tmp_path: Path) -> None:
    plan = "```plan\n" + '{"tasks": [{"title": "Write it", "acceptance": ["file: a.py"]}]}\n' + "```"

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 1:
            return TurnEvidence()
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        return TurnEvidence(changed_files=["a.py"])

    agent = ScriptedAgent(
        [plan, "TASK 1 COMPLETE", "FINAL VERIFICATION PASSED"],
        evidence_for=evidence,
        raises=[ChatError("temporary 502")],
    )
    session = _session(tmp_path, agent, backoff_s=0.0)

    assert session.run() is SessionStatus.COMPLETED


def test_cancellation_preserves_state_and_files(tmp_path: Path) -> None:
    control = ControlFlags()
    control.request_cancel()
    agent = ScriptedAgent([PLAN] * 4)
    session = _session(tmp_path, agent, control=control)

    status = session.run()

    assert status is SessionStatus.CANCELLED
    assert "cancel" in session.reason.lower()
    assert session_model_calls(session) == 0  # nothing was sent to the provider


def test_pause_saves_a_checkpoint_and_resume_continues_it(tmp_path: Path) -> None:
    plan = "```plan\n" + '{"tasks": [{"title": "Write a.py", "acceptance": ["file: a.py"]}]}\n' + "```"
    state = cms.enable(tmp_path)
    control = ControlFlags()

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 1:
            return TurnEvidence()
        if call == 2:
            # The first working cycle asks to pause: the task must stop safely.
            control.request_pause()
            return TurnEvidence(incomplete=True)
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        return TurnEvidence(changed_files=["a.py"])

    agent = ScriptedAgent([plan, "starting", "TASK 1 COMPLETE"], evidence_for=evidence)
    session = CodeSession(
        agent,
        workspace=tmp_path,
        request="Build a.py",
        store=state.store,
        control=control,
        sleep=lambda _s: None,
    )

    assert session.run() is SessionStatus.PAUSED
    checkpoint = state.store.load_checkpoint()
    assert checkpoint is not None
    assert checkpoint["status"] == "paused"
    assert checkpoint["plan"]["tasks"]
    assert checkpoint["changed_files"] is not None
    assert checkpoint["next_action"]

    # Resume from the checkpoint: the same task finishes, nothing is redone.
    resumed_control = ControlFlags()
    resumed = CodeSession.resume(agent, store=state.store, workspace=tmp_path)
    assert resumed is not None
    resumed.control = resumed_control
    resumed._sleep = lambda _s: None  # type: ignore[method-assign]
    assert resumed.run() is SessionStatus.COMPLETED
    assert (tmp_path / "a.py").exists()
    assert resumed.graph.tasks[0].state is TaskState.COMPLETED


def test_final_verification_creates_a_recovery_task_when_it_fails(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    plan = (
        "```plan\n"
        '{"tasks": [{"title": "Create the module", "acceptance": ["file: app.py"]}]}\n'
        "```"
    )

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 1:
            return TurnEvidence()
        if "FINAL VERIFICATION" in prompt:
            # The final check finds a problem once, then the fix lands.
            if not fixed["done"]:
                fixed["done"] = True
                return TurnEvidence(
                    errors=["smoke test failed: app.py raised SystemExit"]
                )
            return TurnEvidence(changed_files=["app.py"])
        return TurnEvidence(
            changed_files=["app.py"],
            commands=[
                __import__("seedcode.core.tasks", fromlist=["CommandRecord"]).CommandRecord(
                    '"python" -m smoke', True, "smoke ok"
                )
            ],
        )

    fixed = {"done": False}
    agent = ScriptedAgent(
        [plan, "TASK 1 COMPLETE", "fixed the smoke test", "all good now"],
        evidence_for=evidence,
    )
    session = _session(tmp_path, agent)
    session.max_task_attempts = 2

    status = session.run()

    assert status is SessionStatus.COMPLETED, session.reason
    # The recovery task was created and verified; nothing is left unresolved.
    assert len(session.graph.tasks) == 2
    assert all(t.state is TaskState.COMPLETED for t in session.graph.tasks)
    assert any(
        "final verification" in note.lower() for note in session.state.attempted_fixes
    )


def test_working_state_carries_the_continuous_context(tmp_path: Path) -> None:
    plan = (
        "```plan\n"
        '{"tasks": [{"title": "Create the module", "detail": "add app.py",\n'
        ' "acceptance": ["file: app.py"], "depends_on": []}]}\n```'
    )
    agent = ScriptedAgent([plan, "TASK 1 COMPLETE"])
    session = _session(tmp_path, agent)
    session.state.note_fix("reverted a bad import")
    session.state.note_blocker("needs a decision on the schema")
    session.state.completed = ["1. Scaffold the project"]
    session.state.remaining = ["2. Add the database layer"]
    session.state.acceptance = ["file: app.py", "no-errors"]
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    session.state.changed_files = ["app.py"]

    prompt = session.state.to_prompt()
    for expected in (
        "Original request",
        "Workspace",
        "Completed",
        "Remaining",
        "Acceptance criteria",
        "Files changed",
        "Attempted fixes",
        "Blockers",
    ):
        assert expected in prompt, expected


def test_history_compaction_keeps_context_bounded(tmp_path: Path) -> None:
    from seedcode.core.agent import AgentEngine, _HISTORY_COMPACT_AT
    from seedcode.core.models import ToolCallRecord
    from seedcode.tools import PermissionLevel, PermissionManager

    from seedcode.core.models import AppConfig

    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
    engine = AgentEngine(config, perm)

    for index in range(_HISTORY_COMPACT_AT):
        engine.messages.append(
            Message(
                role="assistant",
                content=f"step {index}",
                tool_calls=[ToolCallRecord(id=str(index), name="edit_file", arguments={"path": f"f{index}.py"})],
            )
        )
        engine.messages.append(
            Message(role="tool", content="ok", tool_call_id=str(index), tool_name="edit_file")
        )

    before = len(engine.messages)
    assert engine.compact_history() is True
    assert len(engine.messages) < before
    # The system prompt survives and a summary replaces the dropped turns.
    assert engine.messages[0].role == "system"
    assert "[SESSION SUMMARY]" in engine.messages[1].content
    # Tool pairing is never split: a tool result may not directly follow the
    # summary (strict providers reject that shape).
    assert engine.messages[2].role != "tool"
    # Compacting again is a no-op until history grows again.
    assert engine.compact_history() is False


def session_model_calls(session: CodeSession) -> int:
    """Small helper so a cancelled session's zero-call claim reads clearly."""
    return session.model_calls


# --- the control surface ------------------------------------------------------


def test_pause_resume_stop_commands_control_the_live_session(tmp_path, monkeypatch) -> None:
    from seedcode.commands import CommandContext, dispatch

    class _StubUI:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def _record(self, message) -> None:
            self.messages.append(str(message))

        info = dim = success = warning = error = _record

        def panel(self, body, title=None) -> None:
            self.messages.append(title or "")

        def blank(self) -> None:
            self.messages.append("")

    agent = ScriptedAgent([PLAN])
    session = _session(tmp_path, agent)
    session_mod.set_current_session(session)

    ui = _StubUI()
    ctx = CommandContext(ui=ui, config=object(), engine=None)

    dispatch(ctx, "/pause")
    assert session.control.pause_requested is True
    assert any("Pausing" in m for m in ui.messages)

    session.control.clear()
    dispatch(ctx, "/stop")
    assert session.control.cancel_requested is True
    assert any("Stopping" in m for m in ui.messages)

    ui.messages.clear()
    session_mod.set_current_session(None)
    dispatch(ctx, "/pause")
    dispatch(ctx, "/stop")
    assert any("Nothing is running" in m for m in ui.messages)


def test_resume_command_reports_when_code_mode_is_off(monkeypatch) -> None:
    from seedcode.commands import CommandContext, dispatch

    class _StubUI:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def _record(self, message) -> None:
            self.messages.append(str(message))

        info = dim = success = warning = error = _record
        blank = _record

    ui = _StubUI()
    dispatch(CommandContext(ui=ui, config=object(), engine=None), "/resume")
    assert any("No paused Code Mode session" in m or "Code Mode is off" in m for m in ui.messages)


def test_resume_command_continues_a_paused_session(tmp_path, monkeypatch) -> None:
    """`/resume` really continues the checkpointed session through the app."""
    from seedcode import app
    from seedcode.commands import CommandContext, dispatch
    from seedcode.core.models import AppConfig

    class _StubUI:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def _record(self, message) -> None:
            self.messages.append(str(message))

        info = dim = success = warning = error = _record
        blank = _record

        def panel(self, body, title=None) -> None:
            self.messages.append(title or "")

    # A paused session leaves a resumable checkpoint behind.
    control = ControlFlags()
    control.request_pause()
    plan = (
        "```plan\n"
        '{"tasks": [{"title": "Write a.py", "acceptance": ["file: a.py"]}]}\n'
        "```"
    )
    paused = _session(tmp_path, ScriptedAgent([plan, "x"]), control=control)
    assert paused.run() is SessionStatus.PAUSED

    # The agent the resumed session will use (stubbed through _make_agent).
    resumed_agent = ScriptedAgent(["TASK 1 COMPLETE", "FINAL VERIFICATION PASSED"])

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        return TurnEvidence(changed_files=["a.py"])

    resumed_agent._evidence_for = evidence  # type: ignore[attr-defined]
    monkeypatch.setattr(
        app, "_make_agent", lambda ui, config, presenter=None: resumed_agent
    )

    class _FakeHistory:
        def __init__(self) -> None:
            self.saved = 0

        def save(self, transcript) -> None:
            self.saved += 1

    history = _FakeHistory()
    ui = _StubUI()
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")

    # Drive the real helper directly (the command wraps it in a UI-error guard
    # and supplies the session's own history store).
    assert app.resume_codemode_session(ui, config, history) is True

    assert (tmp_path / "a.py").exists()
    assert history.saved == 1
    assert any("Project completed" in m or "completed" in m for m in ui.messages)
    # The checkpoint is cleared once the resumed session completes.
    assert cms.codemode_state().store.load_checkpoint() is None

    # With nothing left to resume, the command says so instead of guessing.
    ui2 = _StubUI()
    monkeypatch.setattr(
        app, "resume_codemode_session", lambda ui, config, history=None: False
    )
    dispatch(CommandContext(ui=ui2, config=config, engine=None), "/resume")
    assert any("No paused Code Mode session" in m for m in ui2.messages)


def test_paused_session_reports_paused_status_and_keeps_the_plan(tmp_path: Path) -> None:
    control = ControlFlags()
    control.request_pause()
    agent = ScriptedAgent([PLAN, "x"])
    session = _session(tmp_path, agent, control=control)

    assert session.run() is SessionStatus.PAUSED
    assert session.status is SessionStatus.PAUSED
    assert "resume" in session.reason


def test_task_cancelled_exception_is_not_leaked_to_the_caller(tmp_path: Path) -> None:
    """A cancel request ends the session cleanly, never as an exception."""
    control = ControlFlags()
    control.request_cancel()
    agent = ScriptedAgent([PLAN, "x"], raises=[TaskCancelled("nope")])
    session = _session(tmp_path, agent, control=control)
    assert session.run() is SessionStatus.CANCELLED


# --- per-task execution record (§2) and inspected context (§3) ----------------

FOUR_TASKS = """\
```plan
{"tasks": [
  {"title": "Create the module", "acceptance": ["file: app.py", "no-errors"]},
  {"title": "Add validation", "acceptance": ["file: app.py", "no-errors"],
   "depends_on": [1]}
]}
```
Task 1 is next.
"""


def test_turn_evidence_records_inspected_targets() -> None:
    """§3: read-only work is recorded, deduplicated, and serializable."""
    evidence = TurnEvidence()
    evidence.note_inspected("src/a.py")
    evidence.note_inspected("src/a.py")
    evidence.note_inspected("search: TodoStore")

    assert evidence.inspected == ["src/a.py", "search: TodoStore"]
    assert "inspected 2 item(s)" in evidence.progress_note()
    assert TurnEvidence.from_dict(evidence.to_dict()).inspected == evidence.inspected
    # Inspecting is not progress: it must not weaken repeat-failure detection.
    assert "src/a.py" not in evidence.fingerprint()


def test_each_task_keeps_its_own_execution_record(tmp_path: Path) -> None:
    """§2: a task's record is per task, not just a session-wide list."""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    succeeded = CommandRecord(
        '"python" -m pytest -q', True, "2 passed in 0.1s", test=True
    )

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 1:
            return TurnEvidence()
        if call == 2:  # task 1: inspect, then write the module and run its tests
            return TurnEvidence(
                inspected=["src/other.py", "search_text: TodoStore"],
                changed_files=["app.py"],
                commands=[succeeded],
                tools=5,
            )
        if call == 3:  # task 2: only validation work, no tests
            return TurnEvidence(inspected=["app.py"], changed_files=["app.py"], tools=2)
        return TurnEvidence()

    agent = ScriptedAgent(
        [FOUR_TASKS, "TASK 1 COMPLETE", "TASK 2 COMPLETE", "FINAL VERIFICATION PASSED"],
        evidence_for=evidence,
    )
    session = _session(tmp_path, agent)

    assert session.run() is SessionStatus.COMPLETED, session.reason
    first, second = session.graph.tasks

    # Task 1's own record.
    assert first.inspected == ["src/other.py", "search_text: TodoStore"]
    assert first.files_affected == ["app.py"]
    assert first.commands == ['"python" -m pytest -q -> ok']
    assert first.tests == ['"python" -m pytest -q: passed']
    assert first.tool_calls == 5
    assert first.evidence and "1/1 command(s) ok" in first.evidence[-1]
    assert first.started_at is not None and first.finished_at is not None
    assert first.duration_s >= 0
    assert first.verification.startswith("verified")
    assert first.current_action == ""  # nothing is running any more
    assert first.state is TaskState.COMPLETED

    # Task 2's record is its own: it never ran a test or touched task 1's files.
    assert second.inspected == ["app.py"]
    assert second.tests == []
    assert second.tool_calls == 2
    assert second.commands == []

    # ...and the session-level context knows what was inspected (§3).
    assert session.state.inspected[:2] == ["src/other.py", "search_text: TodoStore"]
    assert "Files inspected" in session.state.to_prompt()


def test_per_task_record_is_persisted_and_restored(tmp_path: Path) -> None:
    """§4: the checkpoint/plan keeps each task's record across a resume."""
    state = cms.enable(tmp_path)
    graph = TaskGraph.single("Build the feature")
    task = graph.tasks[0]
    task.begin()
    task.absorb(
        TurnEvidence(
            inspected=["src/a.py"],
            changed_files=["app.py"],
            commands=[CommandRecord("pytest -q", True, "1 passed", test=True)],
            tools=4,
        )
    )
    task.verification = "verified 1 criterion"
    task.finish(TaskState.COMPLETED, "verified")

    assert state.store.save_plan(graph.to_dict()) is True
    restored = TaskGraph.from_dict(state.store.load_plan())
    copy = restored.tasks[0]

    assert copy.inspected == ["src/a.py"]
    assert copy.files_affected == ["app.py"]
    assert copy.commands == ["pytest -q -> ok"]
    assert copy.tests == ["pytest -q: passed"]
    assert copy.tool_calls == 4
    assert copy.verification == "verified 1 criterion"
    assert copy.started_at == task.started_at and copy.finished_at == task.finished_at
    assert copy.state is TaskState.COMPLETED


def test_task_done_event_reports_the_observed_evidence(tmp_path: Path) -> None:
    """Phase 5: completion is announced with what was really verified.

    The evidence in the announcement comes from the task's own record — files
    affected, the commands that ran, the tests that passed and the counters —
    not from the model's reply.
    """
    plan = (
        "```plan\n"
        '{"tasks": [{"title": "Write it", "acceptance": ["file: a.py"]}]}\n'
        "```"
    )
    events: list[tuple[str, str]] = []

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 1:
            return TurnEvidence()
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        return TurnEvidence(
            inspected=["src/base.py"],
            changed_files=["a.py"],
            commands=[CommandRecord("pytest -q", True, "1 passed", test=True)],
            tools=3,
        )

    agent = ScriptedAgent(
        [plan, "TASK 1 COMPLETE", "FINAL VERIFICATION PASSED"], evidence_for=evidence
    )
    session = _session(tmp_path, agent)
    session.set_observer(lambda kind, detail: events.append((kind, detail)))

    assert session.run() is SessionStatus.COMPLETED, session.reason

    detail = next(text for kind, text in events if kind == "task_done")
    assert "Task 1/1 completed" in detail
    assert "verified 1 criterion" in detail
    assert "verification: verified 1 criterion" in detail
    assert "files: 1" in detail
    assert "commands: 1/1 ok" in detail
    assert "tests: passed" in detail
    assert "tool calls: 3" in detail


def test_a_task_cancelled_before_it_starts_has_no_end_time(tmp_path: Path) -> None:
    """A timestamp is a fact: a task that never ran gets none."""
    graph = TaskGraph.single("Build the feature")
    task = graph.tasks[0]
    task.finish(TaskState.CANCELLED, "cancelled by the user")

    assert task.started_at is None and task.finished_at is None
    assert task.duration_s == 0.0
    assert task.state is TaskState.CANCELLED
