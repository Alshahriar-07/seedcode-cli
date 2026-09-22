"""v7.1.0 session UI: real state, per-task records, evidence, ASCII fallback.

The task engine keeps a per-task execution record (files inspected and
affected, commands with their outcome, tests, tool calls, retries, timestamps,
verification). These tests lock in that the UI *shows* that state — and only
that state:

* the summary reports the live session (state, progress, calls, evidence) or a
  stopped session's checkpoint, including that it is resumable;
* every task row carries its own observed record, and a task that did nothing
  says so instead of looking successful;
* nothing is fabricated: an unobserved value is an explicit absent mark;
* the same information is ASCII-safe on legacy consoles and never overflows a
  narrow terminal;
* `/session` is the detailed view, `/status` carries the compact session row.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from seedcode import codemode_state as cms
from seedcode.core import session as session_mod
from seedcode.core.session import CodeSession, SessionStatus
from seedcode.core.tasks import (
    CommandRecord,
    TaskGraph,
    TaskState,
    TurnEvidence,
    evidence_summary,
    parse_plan,
)
from seedcode.ui import UI
from seedcode.ui.session_view import (
    session_table,
    session_table as _session_table,  # noqa: F401 - re-exported for symmetry
    summary_rows,
    task_record_cells,
    task_rows,
    tasks_table,
)
from seedcode.ui.theme import SEED_THEME

PLAN = """\
```plan
{"tasks": [
  {"title": "Setup project", "acceptance": ["no-errors"]},
  {"title": "Create database", "acceptance": ["no-errors"], "depends_on": [1]},
  {"title": "Add tests", "acceptance": ["tests"], "depends_on": [2]}
]}
```
"""


def _console(width: int = 100, legacy: bool = False) -> Console:
    return Console(
        theme=SEED_THEME,
        width=width,
        file=io.StringIO(),
        force_terminal=False,
        legacy_windows=legacy,
        record=True,
        highlight=False,
    )


def _text(table, width: int = 100, legacy: bool = False) -> str:
    console = _console(width, legacy)
    console.print(table)
    return console.export_text()


class _StubAgent:
    """Never called: the view only reads state from the session object."""

    messages: list = []
    transcript: list = []
    last_evidence = TurnEvidence()

    def run_turn(self, text: str) -> str:  # pragma: no cover - never used
        return ""


@pytest.fixture(autouse=True)
def _clean_state():
    cms.reset()
    session_mod.reset()
    yield
    cms.reset()
    session_mod.reset()


def _session(tmp_path, request: str = "Build the feature") -> CodeSession:
    state = cms.enable(tmp_path)
    return CodeSession(
        _StubAgent(), workspace=tmp_path, request=request, store=state.store
    )


def _recorded_task(task, *, tests_ok: bool = True) -> None:
    """Give ``task`` a real record by absorbing observed evidence."""
    task.begin()
    task.absorb(
        TurnEvidence(
            inspected=["src/app.py", "search: TodoStore"],
            changed_files=["app.py"],
            commands=[CommandRecord("pytest -q", tests_ok, "", test=True)],
            tools=4,
        )
    )


# --- the summary --------------------------------------------------------------


def test_summary_reports_the_live_session(tmp_path) -> None:
    session = _session(tmp_path)
    session.graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    session.status = SessionStatus.RUNNING
    session.model_calls = 7
    session.tool_calls = 19
    session.recoveries = 2
    first = session.graph.tasks[0]
    _recorded_task(first)
    first.finish(TaskState.COMPLETED, "verified", "verified 2 criteria")
    active = session.graph.tasks[1]
    active.begin()
    active.note_action("running pytest")
    session.session_evidence.merge(
        TurnEvidence(
            inspected=["a.py"],
            changed_files=["app.py"],
            commands=[CommandRecord("pytest -q", True, "3 passed", test=True)],
        )
    )

    rows = dict(summary_rows(session))
    assert rows["State"] == "RUNNING"
    assert rows["Progress"] == "1/3 tasks verified"
    assert rows["Current task"] == "2. Create database"
    assert rows["Action"] == "running pytest"
    assert rows["Model calls"] == "7"
    assert rows["Tool calls"] == "19"
    assert rows["Recoveries"] == "2"
    assert rows["Tests"].startswith("passed (")
    assert rows["Commands"] == "1/1 ok"
    assert rows["Files changed"] == "1"
    assert rows["Files inspected"] == "1"


def test_summary_never_invents_a_result(tmp_path) -> None:
    """No observed evidence reads as absent — never as passed."""
    session = _session(tmp_path)
    session.status = SessionStatus.RUNNING
    rows = dict(summary_rows(session))
    assert rows["Tests"] == "—"
    assert rows["Files changed"] == "—"
    assert rows["Files inspected"] == "—"
    assert "Commands" not in rows


def test_summary_from_a_checkpoint_reports_resumability(tmp_path) -> None:
    checkpoint = {
        "status": "cancelled",
        "progress": {"completed": 1, "total": 3},
        "current_task": {"id": 2, "title": "Create database", "state": "cancelled"},
        "model_calls": 5,
        "tool_calls": 11,
        "next_action": "continue Task 2: Create database",
    }
    rows = dict(summary_rows(None, checkpoint=checkpoint))

    assert rows["State"] == "CANCELLED"
    assert rows["Progress"] == "1/3 tasks verified"
    assert rows["Current task"] == "2. Create database"
    assert "resumable" in rows["Checkpoint"] and "/resume" in rows["Checkpoint"]
    assert rows["Next action"] == "continue Task 2: Create database"


def test_summary_reports_a_blocker_and_the_reason(tmp_path) -> None:
    session = _session(tmp_path)
    session.reason = "Task 2 failed verification"
    session.state.note_blocker("needs a decision on the schema")
    rows = dict(summary_rows(session))
    assert rows["Blockers"] == "needs a decision on the schema"
    assert rows["Reason"] == "Task 2 failed verification"


# --- the per-task records -----------------------------------------------------


def test_task_rows_carry_the_real_record(tmp_path) -> None:
    graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    done, pending, later = graph.tasks
    _recorded_task(done)
    done.finish(TaskState.COMPLETED, "verified", "verified 2 criteria")

    identifier, verification, record = task_record_cells(done)
    assert identifier == "✓ 1"
    assert verification == "verified 2 criteria"
    assert "1 file(s)" in record and "2 inspected" in record
    assert "1/1 cmd ok" in record and "tests passed" in record and "4 calls" in record

    # A pending task that never ran says exactly that.
    assert task_record_cells(pending)[2] == "not started"
    assert task_record_cells(later)[1] == "—"

    # A task that ran but recorded nothing claims nothing.
    empty = graph.tasks[1]
    empty.begin()
    empty.finish(TaskState.BLOCKED, "waiting on the API")
    _, _, empty_record = task_record_cells(empty)
    assert empty_record.startswith("no tool activity recorded")
    assert "blocked: waiting on the API" in empty_record


def test_failed_test_run_is_shown_as_failed(tmp_path) -> None:
    graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    task = graph.tasks[0]
    _recorded_task(task, tests_ok=False)
    _, _, record = task_record_cells(task)
    assert "tests FAILED" in record
    assert "0/1 cmd ok" in record


def test_retry_count_is_reported(tmp_path) -> None:
    graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    task = graph.tasks[0]
    _recorded_task(task)
    task.attempts = 2
    assert "2 retries" in task_record_cells(task)[2]
    task.attempts = 1
    assert "1 retry;" in task_record_cells(task)[2] or "1 retry" in task_record_cells(task)[2]


def test_task_rows_are_in_dependency_order_and_capped(tmp_path) -> None:
    graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    assert [t.title for t in task_rows(graph)] == [
        "Setup project",
        "Create database",
        "Add tests",
    ]
    assert len(task_rows(graph, limit=2)) == 2
    assert task_rows(None) == []


def test_tasks_table_handles_an_empty_plan() -> None:
    out = _text(tasks_table(None))
    assert "no plan yet" in out
    assert "—" in out


# --- evidence summary (core) ---------------------------------------------------


def test_evidence_summary_lists_only_observed_facts(tmp_path) -> None:
    graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    task = graph.tasks[0]
    task.begin()
    assert evidence_summary(task) == "no tool activity recorded"

    _recorded_task(task)
    task.attempts = 1
    task.verification = "verified 2 criteria"
    summary = evidence_summary(task)
    assert "verification: verified 2 criteria" in summary
    assert "files: 1" in summary
    assert "commands: 1/1 ok" in summary
    assert "tests: passed" in summary
    assert "retries: 1" in summary


def test_evidence_summary_marks_a_failed_test_run() -> None:
    graph = TaskGraph.single("x")
    task = graph.tasks[0]
    _recorded_task(task, tests_ok=False)
    assert "tests: failed" in evidence_summary(task)
    assert "commands: 0/1 ok" in evidence_summary(task)


# --- rendering: fallback and width ---------------------------------------------


def test_session_view_is_ascii_on_legacy_consoles(tmp_path) -> None:
    graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    _recorded_task(graph.tasks[0])
    graph.tasks[0].finish(TaskState.COMPLETED, "verified", "verified 2 criteria")
    graph.tasks[1].finish(TaskState.BLOCKED, "waiting")

    out = _text(tasks_table(graph, legacy=True), legacy=True)
    for glyph in "✓●○✗■—•…":
        assert glyph not in out, glyph
    assert "[ok] 1" in out
    assert "!" in out  # the blocked mark
    assert "-" in out  # the absent mark


def test_session_view_never_overflows_a_narrow_console(tmp_path) -> None:
    session = _session(tmp_path)
    session.graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    session.graph.tasks[0].title = "A task title that is deliberately quite long " * 2
    _recorded_task(session.graph.tasks[0])

    for width in (40, 64, 100):
        for table in (
            session_table(session),
            tasks_table(session.graph),
        ):
            console = _console(width)
            console.print(table)
            for line in console.export_text().splitlines():
                assert len(line) <= width, (width, line)


def test_session_view_survives_surrogate_text(tmp_path) -> None:
    """A lone surrogate in a model-written title can never break the view."""
    from seedcode.utils.text import contains_surrogates

    graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    graph.tasks[0].title = f"Fix \ud83d the parser"
    out = _text(tasks_table(graph))
    assert "Fix" in out and "the parser" in out
    assert not contains_surrogates(out)


# --- the /session and /status commands -----------------------------------------


def _ui(width: int = 110) -> UI:
    ui = UI(plain=True)
    ui.console = _console(width)
    return ui


def _dispatch(ui: UI, config, text: str):
    from seedcode.commands import CommandContext, dispatch

    return dispatch(CommandContext(ui=ui, config=config, engine=None), text)


def test_session_command_shows_the_live_session(tmp_path, monkeypatch) -> None:
    from seedcode.core.models import AppConfig

    monkeypatch.chdir(tmp_path)
    session = _session(tmp_path)
    session.graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    session.status = SessionStatus.RUNNING
    session.model_calls = 4
    _recorded_task(session.graph.tasks[0])
    session.graph.tasks[0].finish(TaskState.COMPLETED, "verified", "verified 2 criteria")
    session_mod.set_current_session(session)

    ui = _ui()
    _dispatch(ui, AppConfig(), "/session")
    out = ui.console.export_text()

    assert "Code Mode Session" in out
    assert "RUNNING" in out
    assert "1/3 tasks verified" in out
    assert "Tasks — 1/3 verified" in out
    assert "✓ 1" in out and "Setup project" in out
    assert "verified 2 criteria" in out
    assert "not started" in out


def test_session_command_shows_a_stopped_checkpoint(tmp_path, monkeypatch) -> None:
    from seedcode.core.models import AppConfig

    monkeypatch.chdir(tmp_path)
    state = cms.enable(tmp_path)
    session = CodeSession(
        _StubAgent(), workspace=tmp_path, request="Build the feature", store=state.store
    )
    session.graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    session.status = SessionStatus.CANCELLED
    session.reason = "cancelled by the user"
    session._checkpoint()

    ui = _ui()
    _dispatch(ui, AppConfig(), "/session")
    out = ui.console.export_text()

    assert "CANCELLED" in out
    assert "resumable" in out and "/resume" in out
    assert "Next action" in out
    assert "Setup project" in out


def test_session_command_without_a_session_says_so(tmp_path, monkeypatch) -> None:
    from seedcode.core.models import AppConfig

    monkeypatch.chdir(tmp_path)
    ui = _ui()
    _dispatch(ui, AppConfig(), "/session")
    out = ui.console.export_text()
    assert "No Code Mode session to inspect" in out
    assert "Code Mode Session" not in out


def test_status_carries_the_compact_session_row(tmp_path, monkeypatch) -> None:
    from seedcode.core.models import AppConfig

    monkeypatch.chdir(tmp_path)
    config = AppConfig()
    ui = _ui()
    _dispatch(ui, config, "/status")
    assert "Session" not in ui.console.export_text()  # nothing to report, no row

    session = _session(tmp_path)
    session.graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    session.status = SessionStatus.RUNNING
    session.graph.tasks[0].begin()
    session_mod.set_current_session(session)

    ui2 = _ui()
    _dispatch(ui2, config, "/status")
    out = ui2.console.export_text()
    assert "Session" in out
    assert "RUNNING (0/3 verified) — Task 1" in out


def test_status_session_row_reports_a_resumable_checkpoint(tmp_path, monkeypatch) -> None:
    from seedcode.core.models import AppConfig

    monkeypatch.chdir(tmp_path)
    state = cms.enable(tmp_path)
    state.store.save_checkpoint(
        {
            "status": "paused",
            "progress": {"completed": 2, "total": 3},
            "plan": {"request": "x", "tasks": []},
            "next_action": "continue Task 3",
        }
    )
    ui = _ui()
    _dispatch(ui, AppConfig(), "/status")
    out = ui.console.export_text()
    assert "PAUSED (2/3 verified) — resumable with /resume" in out


# --- the live activity line follows the real phase -----------------------------


def test_presenter_activity_line_follows_the_real_session_phase(tmp_path) -> None:
    """The header's action line reports the real phase, never a decoration."""
    from seedcode import app
    from seedcode.ui.tasks import TaskFlow

    ui = _ui()
    presenter = app._TaskPresenter(ui)
    flow = TaskFlow.for_ui(ui, mode_label="Code Mode", task="Build the app")
    presenter.flow = flow

    session = _session(tmp_path)
    session.graph = TaskGraph.from_plan("x", parse_plan(PLAN))
    session.graph.tasks[0].begin()
    session_mod.set_current_session(session)

    def frame() -> str:
        console = _console()
        console.print(flow._renderable())
        return console.export_text()

    presenter.on_session_event("task_start", "Task 1/3: Setup project")
    assert "Working on: Setup project" in frame()

    presenter.on_session_event("verify", "verifying Task 1")
    assert "Verifying acceptance criteria" in frame()

    presenter.on_session_event("recovery", "Task 1: repairing (attempt 1)")
    assert "Diagnosing the failure and repairing" in frame()

    presenter.on_session_event("final_verify", "running final verification")
    assert "Verifying the project (tests / build)" in frame()


def test_session_command_is_registered_and_documented() -> None:
    from seedcode.commands import _REGISTRY

    assert "session" in _REGISTRY
    assert "Usage: /session" in _REGISTRY["session"][1]
