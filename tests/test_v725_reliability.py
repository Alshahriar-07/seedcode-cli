"""v7.2.5 reliability gaps: task skipping and network-aware pause/reconnect.

These lock in two behaviors the v7.1.0 engine did not have:

* a ``SKIPPED`` task state — a task that is consciously not needed, or whose
  prerequisite can never finish, is *resolved* but never counted as verified
  work, and the project cannot be "complete" from skips alone;
* network-aware recovery — a transient provider failure (a dropped connection,
  a timeout, a 429/5xx) reconnects automatically, and if it cannot, the
  session **pauses with its state and checkpoint intact** instead of reporting
  the task as failed. A permanent error (bad key, unknown model) still fails.

No network: the agent is scripted, tools are real.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedcode import codemode_state as cms
from seedcode.core import session as session_mod
from seedcode.core.chat import ChatError
from seedcode.core.models import Message
from seedcode.core.providers.base import ProviderError
from seedcode.core.session import (
    CodeSession,
    SessionConnectivityError,
    SessionStatus,
)
from seedcode.core.tasks import (
    Task,
    TaskGraph,
    TaskState,
    TurnEvidence,
    glyph_for,
)


@pytest.fixture(autouse=True)
def _clean_state():
    cms.reset()
    session_mod.reset()
    yield
    cms.reset()
    session_mod.reset()


class ScriptedAgent:
    """A scripted agent whose evidence_for decides what each turn "did"."""

    def __init__(self, replies, *, evidence_for=None, on_call=None) -> None:
        self.messages = [Message(role="system", content="system")]
        self.transcript = list(self.messages)
        self.last_evidence = TurnEvidence()
        self.prompts: list[str] = []
        self._replies = list(replies)
        self._evidence_for = evidence_for
        self._on_call = on_call

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def run_turn(self, text: str) -> str:
        self.prompts.append(text)
        if self._on_call is not None:
            self._on_call(self.calls)
        reply = self._replies.pop(0) if self._replies else "done"
        evidence = (
            self._evidence_for(self.calls, text, reply)
            if self._evidence_for
            else TurnEvidence()
        )
        self.last_evidence = evidence
        self.messages.append(Message(role="user", content=text))
        self.messages.append(Message(role="assistant", content=reply))
        self.transcript = list(self.messages)
        return reply


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
  {"title": "Write a.py", "acceptance": ["file: a.py"], "depends_on": []},
  {"title": "Write optional docs", "acceptance": ["file: docs.md"],
   "depends_on": []},
  {"title": "Consume docs", "acceptance": ["file: b.py"], "depends_on": [2]}
]}
```
Task 1 is next.
"""


# --- the SKIPPED state --------------------------------------------------------


def test_skipped_is_resolved_but_never_verified_work() -> None:
    graph = TaskGraph(
        "req",
        [
            Task(id=1, title="Done thing", state=TaskState.COMPLETED),
            Task(id=2, title="Not needed", state=TaskState.SKIPPED),
        ],
    )
    assert graph.all_completed()
    assert graph.unresolved() == []
    assert graph.skipped_count() == 1
    assert graph.resolved_count() == 2

    # Skips alone are not completion: a project needs real verified work.
    only_skips = TaskGraph(
        "req", [Task(id=1, title="Nope", state=TaskState.SKIPPED)]
    )
    assert not only_skips.all_completed()

    # A failed task is not resolved.
    failed = TaskGraph(
        "req",
        [
            Task(id=1, title="Done", state=TaskState.COMPLETED),
            Task(id=2, title="Broke", state=TaskState.FAILED),
        ],
    )
    assert not failed.all_completed()
    assert [t.id for t in failed.unresolved()] == [2]


def test_skipped_state_has_a_glyph_and_does_not_break_graph_roundtrip() -> None:
    assert glyph_for(TaskState.SKIPPED) == "–"
    assert glyph_for(TaskState.SKIPPED, legacy=True) == "-"
    task = Task(id=1, title="T", state=TaskState.SKIPPED)
    assert task.to_dict()["state"] == "skipped"
    assert Task.from_dict(task.to_dict()).state is TaskState.SKIPPED


def test_explicit_skip_and_dependency_skip_complete_the_plan(tmp_path: Path) -> None:
    """Task 2 is declared not needed; task 3, which depends on it, is skipped."""

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        if call == 2:
            (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
            return TurnEvidence(changed_files=["a.py"])
        return TurnEvidence()

    agent = ScriptedAgent(
        [
            PLAN,
            "TASK 1 COMPLETE",
            "TASK 2 SKIPPED: documentation is not required for this request",
            "FINAL VERIFICATION PASSED",
        ],
        evidence_for=evidence,
    )
    session = _session(tmp_path, agent)

    status = session.run()

    assert status is SessionStatus.COMPLETED, session.reason
    assert session.graph.tasks[0].state is TaskState.COMPLETED
    assert session.graph.tasks[1].state is TaskState.SKIPPED
    assert session.graph.tasks[2].state is TaskState.SKIPPED
    # The dependency skip names the prerequisite that did not complete.
    assert "prerequisite task 2" in " ".join(session.graph.tasks[2].notes)
    # A skipped task never counts as completed.
    done, total = session.graph.progress()
    assert (done, total) == (1, 3)


def test_dependency_skip_marks_dependents_when_a_task_fails(tmp_path: Path) -> None:
    """A failed prerequisite does not leave its dependents dangling forever."""
    plan = (
        "```plan\n"
        '{"tasks": [\n'
        '  {"title": "Write a.py", "acceptance": ["file: a.py"], "depends_on": []},\n'
        '  {"title": "Needs a.py", "acceptance": ["file: b.py"], "depends_on": [1]}\n'
        "]}\n```"
    )
    # The model claims completion but never writes a.py: task 1 never verifies.
    agent = ScriptedAgent([plan] * 20, evidence_for=lambda *_: TurnEvidence())
    session = _session(tmp_path, agent)

    status = session.run()

    assert status is SessionStatus.FAILED
    assert session.graph.tasks[0].state in (TaskState.BLOCKED, TaskState.FAILED)
    assert session.graph.tasks[1].state is TaskState.SKIPPED
    assert "prerequisite task 1" in " ".join(session.graph.tasks[1].notes)


# --- network-aware recovery ---------------------------------------------------


class ConnectivityAgent:
    """Plans successfully, then a provider call dies with a transient error."""

    def __init__(self, plan_reply: str, *, fail_from: int = 2, permanent=False):
        self.messages = [Message(role="system", content="system")]
        self.transcript = list(self.messages)
        self.last_evidence = TurnEvidence()
        self.calls = 0
        self._plan = plan_reply
        self._fail_from = fail_from
        self._permanent = permanent

    def run_turn(self, text: str) -> str:
        self.calls += 1
        if self.calls < self._fail_from:
            return self._plan
        cause = ProviderError(
            "connection reset by peer", transient=not self._permanent
        )
        raise ChatError("connection reset by peer") from cause


def test_transient_failure_reconnects_and_resumes_without_restarting(
    tmp_path: Path,
) -> None:
    """A dropped connection retries; the task resumes rather than restarting."""
    plan = (
        "```plan\n"
        '{"tasks": [{"title": "Write a.py", "acceptance": ["file: a.py"]}]}\n'
        "```"
    )

    class RecoveringAgent(ScriptedAgent):
        def run_turn(self, text: str) -> str:
            # call 1: plan; call 2: transient drop; call 3+: recover and work.
            if len(self.prompts) == 1:
                self.prompts.append(text)
                return plan
            if len(self.prompts) == 2:
                self.prompts.append(text)
                cause = ProviderError("temporary 502", transient=True)
                raise ChatError("temporary 502") from cause
            return super().run_turn(text)

    def evidence(call: int, prompt: str, reply: str) -> TurnEvidence:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        return TurnEvidence(changed_files=["a.py"])

    agent = RecoveringAgent(
        ["TASK 1 COMPLETE", "FINAL VERIFICATION PASSED"], evidence_for=evidence
    )
    session = _session(
        tmp_path,
        agent,
        provider_retries=0,
        reconnect_attempts=3,
    )

    status = session.run()

    assert status is SessionStatus.COMPLETED, session.reason
    assert (tmp_path / "a.py").exists()
    # The plan was not re-created on resume: the same task simply continued.
    assert agent.prompts[2].startswith("SESSION STATE") or "TASK 1" in agent.prompts[2]


def test_connectivity_loss_pauses_and_keeps_the_checkpoint(tmp_path: Path) -> None:
    state = cms.enable(tmp_path)
    agent = ConnectivityAgent(PLAN)
    events: list[tuple[str, str]] = []
    session = CodeSession(
        agent,
        workspace=tmp_path,
        request="Build the feature",
        store=state.store,
        on_event=lambda kind, detail: events.append((kind, detail)),
        provider_retries=1,
        reconnect_attempts=2,
        sleep=lambda _s: None,
    )

    status = session.run()

    assert status is SessionStatus.PAUSED, session.reason
    assert "connection" in session.reason.lower()
    # It genuinely tried to reconnect before giving up.
    kinds = [kind for kind, _ in events]
    assert "reconnect" in kinds
    assert "network_lost" in kinds
    # State and files are preserved: the plan exists and nothing was written.
    assert session.graph.tasks
    assert not (tmp_path / "a.py").exists()
    checkpoint = state.store.load_checkpoint()
    assert checkpoint is not None
    assert checkpoint["status"] == "paused"
    assert checkpoint["next_action"]


def test_permanent_provider_error_is_not_treated_as_connectivity(
    tmp_path: Path,
) -> None:
    agent = ConnectivityAgent(PLAN, permanent=True)
    session = _session(tmp_path, agent, provider_retries=1, reconnect_attempts=2)

    status = session.run()

    # A non-transient failure is a real failure, with the original wording.
    assert status is SessionStatus.FAILED
    assert "provider request failed" in session.reason


def test_connectivity_error_is_a_provider_error_subclass() -> None:
    assert issubclass(SessionConnectivityError, session_mod.SessionProviderError)


def test_presenter_renders_the_new_session_events() -> None:
    """The new event kinds are handled without raising (UI wiring guard)."""
    from seedcode import app

    class DummyUI:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def dim(self, text: str) -> None:
            self.lines.append(f"dim:{text}")

        def warning(self, text: str) -> None:
            self.lines.append(f"warn:{text}")

        def success(self, text: str) -> None:
            self.lines.append(f"ok:{text}")

        def error(self, text: str) -> None:
            self.lines.append(f"err:{text}")

        def blank(self) -> None:
            self.lines.append("")

    ui = DummyUI()
    presenter = app._TaskPresenter(ui)  # type: ignore[arg-type]
    # None of these may raise, and each must render something.
    for kind in ("reconnect", "reconnected", "network_lost", "task_skipped"):
        presenter.on_session_event(kind, "detail")
    warns = [line for line in ui.lines if line.startswith("warn:")]
    dims = [line for line in ui.lines if line.startswith("dim:")]
    assert len(warns) == 2  # reconnect + network_lost
    assert len(dims) == 2  # reconnected + task_skipped
    # A skip is visibly distinct from ordinary narration.
    assert any("–" in line or "-" in line for line in dims)
