"""Task-progress flow tests (v6.2.5 Code / Assist / Agent mode UX).

Two things are locked in here:

* **The step view is driven by real activity.** A step only reaches
  ``completed`` when the engine actually reported the work (a tool call that
  ran, a narration message, a finished test command); anything the task did not
  do ends as ``skipped``. There is no fake progress anywhere.
* **A finished task never ends the CLI.** Every outcome — success, failure,
  cancellation — returns to the prompt with a clear status line, so the user
  can inspect the result, run another task, switch mode/provider, or exit
  manually.

No network: providers are scripted, and the tools run against ``tmp_path``.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from rich.console import Console

from seedcode import app
from seedcode import codemode_state as cms
from seedcode.core.agent import AgentEngine
from seedcode.core.chat import ChatEngine, ChatError
from seedcode.core.lifecycle import lifecycle
from seedcode.core.models import AppConfig, Message
from seedcode.tools import PermissionLevel, PermissionManager
from seedcode.ui import UI
from seedcode.core.tasks import TurnEvidence
from seedcode.ui.tasks import TaskFlow, TaskState, _test_summary, step_glyph
from seedcode.ui.theme import SEED_THEME


# --- helpers -----------------------------------------------------------------


def _console(width: int = 100) -> Console:
    return Console(
        theme=SEED_THEME,
        width=width,
        file=io.StringIO(),
        force_terminal=False,
        legacy_windows=False,
        record=True,
        highlight=False,
    )


def _ui(width: int = 100) -> UI:
    ui = UI(plain=True)
    ui.console = _console(width)
    return ui


class _StubUI:
    """The minimal UI surface the presenter uses (records what it is told)."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def _record(self, message) -> None:
        self.messages.append(str(message))

    info = dim = success = warning = error = _record

    def blank(self) -> None:
        self.messages.append("")


def _block(tool: str, args: dict) -> str:
    return "```tool\n" + json.dumps({"tool": tool, "args": args}) + "\n```"


@pytest.fixture(autouse=True)
def _clean_state():
    lifecycle().reset_for_tests()
    cms.reset()
    yield
    cms.reset()
    lifecycle().reset_for_tests()


# --- the abstract step states ------------------------------------------------


def test_glyphs_cover_every_state() -> None:
    assert step_glyph(TaskState.PENDING) == "○"
    assert step_glyph(TaskState.RUNNING) == "●"
    assert step_glyph(TaskState.COMPLETED) == "✓"
    assert step_glyph(TaskState.FAILED) == "✗"
    assert step_glyph(TaskState.SKIPPED) == "–"


def test_legacy_console_gets_ascii_glyphs() -> None:
    assert step_glyph(TaskState.COMPLETED, legacy=True) == "[ok]"
    assert step_glyph(TaskState.FAILED, legacy=True) == "[x]"


def test_for_ui_without_a_console_returns_none() -> None:
    """Test doubles and embedders simply get no task view (never a crash)."""
    assert TaskFlow.for_ui(_StubUI(), mode_label="Code Mode", task="x") is None
    assert TaskFlow.for_ui(_ui(), mode_label="Code Mode", task="x") is not None


def test_flow_registers_its_live_display_so_dialogs_can_pause_it() -> None:
    ui = _ui()
    flow = TaskFlow.for_ui(ui, mode_label="Code Mode", task="x")
    flow.start()
    assert ui._live is flow._live  # a permission dialog pauses this display
    flow.stop()
    assert ui._live is None


def test_live_view_names_the_running_step() -> None:
    console = _console()
    flow = TaskFlow(console, mode_label="Code Mode", task="t").begin()
    flow.observe_tool_start("edit_file", {"path": "a.py"})
    console.print(flow._renderable())
    assert "Working… (implement changes)" in console.export_text()


# --- real activity drives the steps ------------------------------------------


def _headless_flow(task: str = "Fix the bug") -> TaskFlow:
    return TaskFlow(None, mode_label="Code Mode", task=task)


def test_begin_only_starts_the_first_step() -> None:
    flow = _headless_flow().begin()
    assert flow.step("analyze").state is TaskState.RUNNING
    assert flow.step("inspect").state is TaskState.PENDING
    assert flow.step("test").state is TaskState.PENDING


def test_progress_follows_real_tool_activity() -> None:
    flow = _headless_flow().begin()

    flow.observe_tool_start("read_file", {"path": "a.py"})
    assert flow.step("analyze").state is TaskState.COMPLETED
    assert flow.step("inspect").state is TaskState.RUNNING

    flow.observe_tool_done("read_file", True, "file body", {"path": "a.py"})
    assert flow.step("inspect").state is TaskState.COMPLETED

    flow.observe_text("I will change the parser.")
    assert flow.step("plan").state is TaskState.COMPLETED

    flow.observe_tool_start("edit_file", {"path": "a.py"})
    assert flow.step("implement").state is TaskState.RUNNING
    flow.observe_tool_done("edit_file", True, "edited", {"path": "a.py"})
    assert flow.step("implement").state is TaskState.COMPLETED
    assert "1 file(s) changed" in flow.step("implement").detail


def test_skipped_steps_are_never_reported_as_done() -> None:
    flow = _headless_flow().begin()
    # A task that jumps straight to an edit never "inspected" or "planned".
    flow.observe_tool_start("write_file", {"path": "new.py"})
    flow.observe_tool_done("write_file", True, "wrote", {"path": "new.py"})

    assert flow.step("inspect").state is TaskState.SKIPPED
    assert flow.step("plan").state is TaskState.SKIPPED
    assert flow.step("implement").state is TaskState.COMPLETED


def test_tests_step_requires_a_real_test_command() -> None:
    flow = _headless_flow().begin()
    # A build command is not a test run.
    flow.observe_tool_start("run_command", {"command": "npm run build"})
    assert flow.step("test").state is TaskState.PENDING
    flow.observe_tool_done("run_command", True, "built")
    assert flow.step("test").state is TaskState.PENDING

    flow.observe_tool_start("run_command", {"command": "pytest tests -q"})
    assert flow.step("test").state is TaskState.RUNNING
    flow.observe_tool_done("run_command", True, "721 passed in 12s")
    assert flow.step("test").state is TaskState.COMPLETED
    assert "721 passed" in flow.step("test").detail


def test_failing_test_run_is_a_failed_step_not_a_pass() -> None:
    flow = _headless_flow().begin()
    flow.observe_tool_start("run_command", {"command": "go test ./..."})
    flow.observe_tool_done("run_command", False, "FAIL example.com/x 3 failed, 718 passed")
    test_step = flow.step("test")
    assert test_step.state is TaskState.FAILED
    assert "3 failed" in test_step.detail
    assert "718 passed" not in test_step.detail
    # Nothing was edited, so the change step is skipped rather than claimed.
    assert flow.step("implement").state is TaskState.SKIPPED


def test_test_summary_parses_real_output_or_says_exit_zero() -> None:
    assert _test_summary("721 passed in 13.87s", True) == "721 passed"
    assert _test_summary("12 passing (40ms)", True) == "12 passing"
    assert _test_summary("test result: ok. 5 passed; 0 failed", True) == "5 passed"
    # No parseable count: report the exit status honestly instead of a number.
    assert _test_summary("pytest 9.1.1", True) == "exit 0"
    # A failed run never reports a passing count it did not earn.
    assert _test_summary("3 failed, 718 passed", False) == "3 failed"
    assert _test_summary("boom", False) == "test run failed"


def test_failed_step_stays_visible_after_finish() -> None:
    console = _console()
    flow = TaskFlow(console, mode_label="Code Mode", task="Break it").begin()
    flow.observe_tool_start("edit_file", {"path": "a.py"})
    flow.observe_tool_done("edit_file", False, "old_text not found")
    flow.finish("completed")

    out = console.export_text()
    assert "✗ Implement changes" in out
    assert "1 step(s) failed" in out
    assert "Task completed" in out


# --- outcomes ----------------------------------------------------------------


def test_success_outcome_is_persistent_and_prompts_for_more() -> None:
    console = _console()
    flow = TaskFlow(console, mode_label="Code Mode", task="Fix auth").begin()
    flow.observe_tool_start("write_file", {"path": "config.py"})
    flow.observe_tool_done("write_file", True, "wrote", {"path": "config.py"})
    flow.observe_tool_start("run_command", {"command": "pytest -q"})
    flow.observe_tool_done("run_command", True, "721 passed")
    flow.update("verify", TaskState.COMPLETED, "answer produced")
    flow.finish("completed")

    out = console.export_text()
    assert "Task  ·  Code Mode" in out
    assert "✓ Task completed" in out
    assert "721 passed" in out
    assert "Ready for next task." in out


def test_failure_outcome_shows_the_reason_and_stays_usable() -> None:
    console = _console()
    flow = TaskFlow(console, mode_label="Agent Mode", task="Do the thing").begin()
    flow.finish("failed", "Provider rejected the request")

    out = console.export_text()
    assert "✗ Task failed" in out
    assert "Provider rejected the request" in out
    assert "Ready for another task." in out


def test_cancelled_outcome_is_reported() -> None:
    console = _console()
    flow = TaskFlow(console, mode_label="Agent Mode", task="Long job").begin()
    flow.finish("cancelled")

    out = console.export_text()
    assert "Task cancelled" in out
    assert "Ready for next task." in out


def test_finish_is_idempotent_and_pending_steps_become_skipped() -> None:
    console = _console()
    flow = TaskFlow(console, mode_label="Code Mode", task="Tiny").begin()
    flow.finish("completed")
    flow.finish("completed")
    assert flow.step("test").state is TaskState.SKIPPED
    assert console.export_text().count("Task completed") == 1


def test_no_tests_run_means_no_test_claim() -> None:
    console = _console()
    flow = TaskFlow(console, mode_label="Code Mode", task="Tiny").begin()
    flow.observe_text("Just answering.")
    flow.finish("completed")
    out = console.export_text()
    assert "Tests:" not in out
    assert "– Run tests" in out


# --- the presenter -----------------------------------------------------------


def test_presenter_routes_engine_activity_into_the_flow() -> None:
    ui = _StubUI()
    presenter = app._TaskPresenter(ui)
    flow = _headless_flow().begin()
    presenter.flow = flow

    presenter.on_step({"phase": "tool_start", "name": "read_file", "args": {"path": "a"}})
    presenter.on_step({"phase": "tool_done", "name": "read_file", "args": {"path": "a"}, "ok": True, "output": "body"})

    assert flow.step("inspect").state is TaskState.COMPLETED


def test_presenter_quiets_tool_chatter_when_a_flow_is_on_screen() -> None:
    ui = _StubUI()
    presenter = app._TaskPresenter(ui)
    presenter.on_event("call", 'read_file({"path": "a"})')
    assert any("read_file" in m for m in ui.messages)  # narrated without a flow

    ui.messages.clear()
    presenter.flow = _headless_flow().begin()
    presenter.on_event("call", 'read_file({"path": "a"})')
    assert ui.messages == []  # the step list already shows it


def test_presenter_never_hides_errors() -> None:
    ui = _StubUI()
    presenter = app._TaskPresenter(ui)
    presenter.flow = _headless_flow().begin()
    presenter.on_event("error", "permission denied\nsecond line")
    assert any("permission denied" in m for m in ui.messages)


def test_live_command_output_echo_is_bounded() -> None:
    ui = _StubUI()
    presenter = app._TaskPresenter(ui)
    for index in range(40):
        presenter.on_output(f"line {index}")
    echoed = [m for m in ui.messages if m.startswith("  │ line")]
    assert len(echoed) == presenter.MAX_ECHO_LINES
    assert any("output continues" in m for m in ui.messages)


def test_presenter_resets_the_echo_for_each_command() -> None:
    ui = _StubUI()
    presenter = app._TaskPresenter(ui)
    presenter.flow = _headless_flow().begin()
    for index in range(20):
        presenter.on_output(f"line {index}")
    presenter.on_step({"phase": "tool_start", "name": "run_command", "args": {"command": "ls"}})
    presenter.on_output("fresh line")
    assert any(m == "  │ fresh line" for m in ui.messages)


# --- the engine's structured events ------------------------------------------


def _scripted_engine(
    workspace: Path,
    replies: list[str],
    *,
    on_event=None,
    on_step=None,
) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)
    engine = AgentEngine(config, perm, on_event=on_event, on_step=on_step)
    engine._native = False  # text protocol: no provider calls
    queue = iter(replies)

    def _reply():
        try:
            return iter([next(queue)])
        except StopIteration:
            return iter([""])

    engine.stream_reply = _reply  # type: ignore[method-assign]
    return engine


def test_engine_reports_structured_tool_events(tmp_path: Path) -> None:
    steps: list[dict] = []
    engine = _scripted_engine(
        tmp_path,
        [_block("read_file", {"path": "hello.py"}), "done"],
        on_step=steps.append,
    )
    engine.run_turn("look at hello.py")

    starts = [s for s in steps if s["phase"] == "tool_start"]
    dones = [s for s in steps if s["phase"] == "tool_done"]
    assert [s["name"] for s in starts] == ["read_file"]
    assert starts[0]["args"] == {"path": "hello.py"}
    assert dones and dones[0]["name"] == "read_file" and dones[0]["ok"] is False


def test_engine_and_flow_track_a_real_turn(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cms.enable(tmp_path)
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")

    ui = _StubUI()
    presenter = app._TaskPresenter(ui)
    flow = _headless_flow().begin()
    presenter.flow = flow

    engine = _scripted_engine(
        tmp_path,
        [
            "I'll read the file, then update it.\n"
            + _block("read_file", {"path": "hello.py"}),
            _block(
                "edit_file",
                {
                    "path": "hello.py",
                    "old_text": "print('hi')",
                    "new_text": "print('hello')",
                },
            ),
            _block("run_command", {"command": f'"{sys.executable}" -m pytest --version'}),
            "Updated hello.py and ran pytest.",
        ],
        on_event=presenter.on_event,
        on_step=presenter.on_step,
    )
    engine.run_turn("Update hello.py")

    assert flow.step("analyze").state is TaskState.COMPLETED
    assert flow.step("inspect").state is TaskState.COMPLETED
    assert flow.step("plan").state is TaskState.COMPLETED
    assert flow.step("implement").state is TaskState.COMPLETED
    assert flow.step("test").state is TaskState.COMPLETED
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "print('hello')\n"


# --- the CLI stays alive around an agent turn --------------------------------


class _FakeAgent:
    """A stand-in engine: only what ``_handle_agent`` touches."""

    def __init__(self, reply: str = "All done.", raises: BaseException | None = None):
        self.config = AppConfig(agent_mode=True)
        self.messages = [Message(role="system", content="system")]
        self.transcript = list(self.messages)
        self._reply = reply
        self._raises = raises

    def run_turn(self, text: str) -> str:
        if self._raises is not None:
            raise self._raises
        self.messages.append(Message(role="assistant", content=self._reply))
        return self._reply


class _FakeHistory:
    def __init__(self) -> None:
        self.saved = 0

    def save(self, transcript) -> None:
        self.saved += 1


def _capture_flows(monkeypatch) -> list[TaskFlow]:
    """Record every TaskFlow ``_handle_agent`` creates for assertions."""
    created: list[TaskFlow] = []
    original = TaskFlow.for_ui.__func__

    def spy(cls, ui, **kwargs):
        flow = original(cls, ui, **kwargs)
        created.append(flow)
        return flow

    monkeypatch.setattr(TaskFlow, "for_ui", classmethod(spy))
    return created


def _run_turn(ui, agent, history, text: str = "Fix the bug") -> None:
    """One turn exactly as the REPL runs it (inside the lifecycle span)."""
    with lifecycle().task_span():
        app._handle_agent(ui, agent, history, text)


def test_handle_agent_completes_and_returns_to_the_prompt(monkeypatch) -> None:
    ui = _ui()
    flows = _capture_flows(monkeypatch)
    history = _FakeHistory()

    _run_turn(ui, _FakeAgent("Fixed it."), history)

    out = ui.console.export_text()
    assert "Task completed" in out
    assert "Ready for next task." in out
    assert flows and flows[0].step("verify").state is TaskState.COMPLETED
    # The session is still alive and at the prompt — nothing exited.
    assert lifecycle().is_running()
    assert lifecycle().phase.value == "idle"
    assert history.saved == 1


def test_handle_agent_reports_a_provider_failure_and_stays_open(monkeypatch) -> None:
    ui = _ui()
    flows = _capture_flows(monkeypatch)

    _run_turn(ui, _FakeAgent(raises=ChatError("no route to host")), _FakeHistory(), "Do it")

    out = ui.console.export_text()
    assert "no route to host" in out
    assert "Task failed" in out
    assert "Ready for another task." in out
    assert lifecycle().is_running()


def test_handle_agent_reports_a_cancelled_task_and_stays_open(monkeypatch) -> None:
    ui = _ui()
    _capture_flows(monkeypatch)

    _run_turn(ui, _FakeAgent(raises=KeyboardInterrupt()), _FakeHistory(), "Do it")

    out = ui.console.export_text()
    assert "Task cancelled" in out
    assert "Ready for next task." in out
    assert lifecycle().is_running()


def test_handle_agent_survives_an_unexpected_engine_error(monkeypatch) -> None:
    """A crash inside the engine is reported, not fatal — the loop continues."""
    ui = _ui()
    _capture_flows(monkeypatch)

    _run_turn(ui, _FakeAgent(raises=RuntimeError("boom")), _FakeHistory(), "Do it")

    out = ui.console.export_text()
    assert "Task failed" in out
    assert "RuntimeError" in out
    assert lifecycle().is_running()


def test_chat_loop_passes_the_presenter_and_never_exits_on_a_task() -> None:
    """The REPL handing a turn to the engine must not decide to quit."""
    import inspect

    source = inspect.getsource(app._chat_loop)
    assert "_handle_agent(ui, agent, history, text, presenter)" in source
    assert "_make_agent(ui, config, presenter)" in source
    # No exit path anywhere in the loop body.
    assert "sys.exit" not in source
    assert "request_exit" not in source


class _ScriptedSession:
    """A prompt session that returns scripted lines, then EOF."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self.prompts = 0

    def prompt(self, message=None, **_kwargs) -> str:
        self.prompts += 1
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


class _RecordingAgent:
    """Engine double that records the tasks it was handed.

    It reports real evidence (a changed file) for every turn, because the
    persistent Code Mode session verifies tasks against evidence — a double
    that only returns text would be reported as an unverified task.
    """

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.config = AppConfig(agent_mode=True)
        self.messages = [Message(role="system", content="system")]
        self.transcript = list(self.messages)
        self.last_evidence = TurnEvidence(changed_files=["app.py"])

    def run_turn(self, text: str) -> str:
        self.calls.append(text)
        self.last_evidence = TurnEvidence(changed_files=["app.py"])
        return f"Finished: {text}"


@pytest.mark.parametrize("code_mode", [False, True])
def test_chat_loop_runs_task_after_task_and_exits_only_on_command(
    monkeypatch, tmp_path: Path, code_mode: bool
) -> None:
    """Agent Mode and Code Mode: task, task, manual exit — CLI alive throughout."""
    if code_mode:
        cms.enable(tmp_path)
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")

    calls: list[str] = []
    monkeypatch.setattr(
        app, "_make_agent", lambda ui, config, presenter=None: _RecordingAgent(calls)
    )

    ui = _ui()
    session = _ScriptedSession(["fix the bug", "now run the tests", "/exit"])
    app._chat_loop(ui, config, ChatEngine(config), _FakeHistory(), session)

    # Both turns really ran. In Code Mode the session wraps the request in a
    # plan prompt and a task prompt (that is the point of v7.1.0), so the
    # request is asserted to have been carried into a call, not to be the
    # literal argument.
    if code_mode:
        assert any("fix the bug" in c for c in calls)
        assert any("now run the tests" in c for c in calls)
    else:
        assert calls == ["fix the bug", "now run the tests"]
    assert session.prompts == 3  # the third prompt was /exit, not an EOF
    assert lifecycle().is_running()  # /exit only returns to the menu
    out = ui.console.export_text()
    assert out.count("Ready for next task.") == 2
    assert out.count("Task completed") == 2


def test_task_paths_never_terminate_the_process() -> None:
    """No module on the task path may call exit/sys.exit/os._exit."""
    import inspect

    from seedcode.core import agent as agent_mod
    from seedcode.ui import tasks as tasks_mod

    for module in (app, agent_mod, tasks_mod):
        source = inspect.getsource(module)
        assert "sys.exit" not in source, module.__name__
        assert "os._exit" not in source, module.__name__
        assert "raise SystemExit" not in source, module.__name__

    # cli.py may exit only on a fatal error (never after a completed task).
    from seedcode import cli

    cli_source = inspect.getsource(cli)
    assert cli_source.count("sys.exit(") == 1
    assert "Fatal error" in cli_source
