"""v7.1.0 end-to-end: Code Mode really finishes a multi-step project.

This is the test the release is judged by. Nothing about the agent loop is
mocked: a real :class:`~seedcode.core.agent.AgentEngine` runs the real tool
registry (file writes, edits, ``run_command`` with real subprocesses) inside a
throwaway project, driven by a deterministic *coding model* that behaves like
one — it plans, writes code, runs the tests, reads the failure, fixes the code
and re-runs until the suite passes.

The request is a small full-stack-ish todo app (persistence, validation, a CLI,
and tests), which cannot be finished in one model call. The test proves:

* a plan with several TODOs is created and executed in dependency order;
* TODO 1 finishes, then TODO 2 starts automatically, then TODO 3;
* each task takes *multiple* model/tool cycles (never one call per TODO);
* a real failing ``pytest`` run is inspected and fixed, and the task is only
  completed after the re-run passes;
* final verification runs the project's own tests;
* the finished project really passes when pytest is run against it afterwards.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from seedcode import app, codemode_state as cms
from seedcode.core import session as session_mod
from seedcode.core.agent import AgentEngine
from seedcode.core.lifecycle import lifecycle
from seedcode.core.models import AppConfig
from seedcode.core.session import SessionStatus
from seedcode.core.tasks import TaskState
from seedcode.tools import PermissionLevel, PermissionManager
from seedcode.ui import UI
from seedcode.ui.theme import SEED_THEME


# --- the project the agent will build ----------------------------------------

STORE_V1 = '''\
"""Minimal todo store with JSON persistence."""
import json
from pathlib import Path


class TodoStore:
    def __init__(self, path):
        self.path = Path(path)
        self.items = self._load()

    def _load(self):
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8") or "[]")

    def save(self):
        self.path.write_text(json.dumps(self.items), encoding="utf-8")

    def add(self, title):
        item = {"title": title, "done": False}
        self.items.append(item)
        self.save()
        return item
'''

ADD_OLD = '    def add(self, title):\n        item = {"title": title, "done": False}\n'
ADD_NEW = (
    "    def add(self, title):\n"
    "        if not (title or '').strip():\n"
    "            raise ValueError('title must not be empty')\n"
    '        item = {"title": title.strip(), "done": False}\n'
)

CLI = '''\
"""Tiny todo CLI."""
import sys

from todo_app import TodoStore


def main(argv):
    store = TodoStore("todos.json")
    if len(argv) > 1 and argv[1] == "add":
        store.add(" ".join(argv[2:]))
        print(f"added {len(store.items)} todo(s)")
        return 0
    for index, item in enumerate(store.items, 1):
        print(f"{index}. {item['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''

TESTS = '''\
import pytest

from todo_app import TodoStore


def test_add_and_persist(tmp_path):
    store = TodoStore(tmp_path / "todos.json")
    store.add("write tests")
    assert TodoStore(tmp_path / "todos.json").items[0]["title"] == "write tests"


def test_empty_title_is_rejected(tmp_path):
    store = TodoStore(tmp_path / "todos.json")
    with pytest.raises(ValueError):
        store.add("   ")
'''

PLAN_REPLY = """\
I inspected the repository. Here is the implementation plan.

```plan
{"tasks": [
  {"title": "Create the todo store",
   "detail": "todo_app.py: JSON persistence with a TodoStore class",
   "acceptance": ["file: todo_app.py", "no-errors"], "depends_on": []},
  {"title": "Add the CLI layer",
   "detail": "cli.py using the store",
   "acceptance": ["file: cli.py", "no-errors"], "depends_on": [1]},
  {"title": "Add tests and make them pass",
   "detail": "test_todo_app.py covering persistence and validation",
   "acceptance": ["file: test_todo_app.py", "tests"], "depends_on": [2]}
]}
```

Task 1 is next.
"""

PY = f'"{sys.executable}"'


def _block(tool: str, args: dict) -> str:
    return "```tool\n" + json.dumps({"tool": tool, "args": args}) + "\n```"


class CodingModel:
    """A deterministic coding model driving the real agent loop.

    It behaves like a competent model: plan first, then per task write code and
    run it; when a command fails, read the failure and fix the code before
    claiming the task is complete.
    """

    def __init__(self) -> None:
        self.model_calls = 0
        self.tool_calls = 0
        self.task_prompts: list[int] = []
        self.failures_seen = 0
        self._current_task = 0
        self._fixed_store = False

    # --- the fake provider ---------------------------------------------------
    def reply(self, engine: AgentEngine) -> str:
        self.model_calls += 1
        last = engine.messages[-1]
        content = last.content or ""

        if "implementation plan for THIS repository" in content:
            return PLAN_REPLY

        if "FINAL VERIFICATION" in content:
            if last.role in ("tool",) or content.startswith("[TOOL RESULTS]"):
                if self._saw_failure(content):
                    return _block(
                        "edit_file",
                        {"path": "todo_app.py", "old_text": ADD_OLD, "new_text": ADD_NEW},
                    ) + "\n" + _block("run_command", {"command": f"{PY} -m pytest -q"})
                return "FINAL VERIFICATION PASSED"
            return _block("run_command", {"command": f"{PY} -m pytest -q"}) + "\n" + _block(
                "run_command", {"command": f'{PY} -c "import cli, todo_app"'}
            )

        tool_results = last.role == "tool" or content.startswith("[TOOL RESULTS]")
        task = self._current_task

        if tool_results:
            if self._saw_failure(content):
                self.failures_seen += 1
                if task == 3 and not self._fixed_store and "NOT verified" not in content:
                    # The dishonest-model case, deliberately: it claims the task
                    # is complete although the tests just failed. The session
                    # must reject the claim and ask for a real fix.
                    return f"TASK {task} COMPLETE"
                if task == 3 and not self._fixed_store:
                    # The repair request arrived: fix the code and re-run.
                    self._fixed_store = True
                    return (
                        _block("read_file", {"path": "todo_app.py"})
                        + "\n"
                        + _block(
                            "edit_file",
                            {
                                "path": "todo_app.py",
                                "old_text": ADD_OLD,
                                "new_text": ADD_NEW,
                            },
                        )
                        + "\n"
                        + _block("run_command", {"command": f"{PY} -m pytest -q"})
                    )
                return f"TASK {task} COMPLETE"
            return f"TASK {task} COMPLETE"

        # A fresh task prompt — or a repair request for the task in flight.
        task = self._task_number(content) or self._current_task
        self._current_task = task
        if "Implement TASK " in content:
            self.task_prompts.append(task)
        if "NOT verified" in content:
            # The session rejected the claim: fix the real cause and re-run.
            if not self._fixed_store:
                self._fixed_store = True
                return (
                    _block("read_file", {"path": "todo_app.py"})
                    + "\n"
                    + _block(
                        "edit_file",
                        {
                            "path": "todo_app.py",
                            "old_text": ADD_OLD,
                            "new_text": ADD_NEW,
                        },
                    )
                    + "\n"
                    + _block("run_command", {"command": f"{PY} -m pytest -q"})
                )
            return f"TASK {task} COMPLETE"
        if task == 1:
            return (
                "I'll build the store first.\n"
                + _block("write_file", {"path": "todo_app.py", "content": STORE_V1})
                + "\n"
                + _block("run_command", {"command": f'{PY} -c "import todo_app; print(1)"'})
            )
        if task == 2:
            return (
                "Now the CLI.\n"
                + _block("write_file", {"path": "cli.py", "content": CLI})
                + "\n"
                + _block("run_command", {"command": f'{PY} -c "import cli; print(1)"'})
            )
        if task == 3:
            return (
                "Writing the tests and running them.\n"
                + _block("write_file", {"path": "test_todo_app.py", "content": TESTS})
                + "\n"
                + _block("run_command", {"command": f"{PY} -m pytest -q"})
            )
        return "Nothing left to do."

    # --- helpers -------------------------------------------------------------
    @staticmethod
    def _task_number(prompt: str) -> int:
        for line in prompt.splitlines():
            if line.startswith("Implement TASK "):
                try:
                    return int(line.split()[2].rstrip(":"))
                except (IndexError, ValueError):
                    return 0
        return 0

    @staticmethod
    def _saw_failure(text: str) -> bool:
        lowered = text.lower()
        return (
            "failed (exit code" in lowered
            or "1 failed" in lowered
            or "error" in lowered and "failed" in lowered
        )


def _engine(workspace: Path, model: CodingModel, ui=None, events=None) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)
    steps = [] if events is None else events
    engine = AgentEngine(
        config,
        perm,
        on_event=(lambda k, d: steps.append((k, d))) if events is not None else None,
    )
    engine._native = False  # text protocol: no provider, real tools

    def stream_reply():
        return iter([model.reply(engine)])

    engine.stream_reply = stream_reply  # type: ignore[method-assign]
    return engine


@pytest.fixture(autouse=True)
def _clean_state():
    cms.reset()
    session_mod.reset()
    lifecycle().reset_for_tests()
    yield
    cms.reset()
    session_mod.reset()
    lifecycle().reset_for_tests()


def _console_ui(width: int = 100):
    import io

    from rich.console import Console

    ui = UI(plain=True)
    ui.console = Console(
        theme=SEED_THEME,
        width=width,
        file=io.StringIO(),
        force_terminal=False,
        legacy_windows=False,
        record=True,
        highlight=False,
    )
    return ui


# --- the realistic run --------------------------------------------------------


def test_codemode_builds_the_todo_app_over_many_cycles(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    state = cms.enable(tmp_path)
    model = CodingModel()
    events: list[tuple[str, str]] = []
    engine = _engine(tmp_path, model, events=events)

    session_events: list[tuple[str, str]] = []
    session = session_mod.CodeSession(
        engine,
        workspace=tmp_path,
        request=(
            "Create a small todo application with a JSON store, validation, a "
            "CLI and tests."
        ),
        store=state.store,
        on_event=lambda kind, detail: session_events.append((kind, detail)),
        sleep=lambda _s: None,
    )

    status = session.run()

    assert status is SessionStatus.COMPLETED, session.reason
    # --- a plan with several TODOs, executed in order -------------------------
    titles = [t.title for t in session.graph.tasks]
    assert len(titles) >= 3, titles
    assert all(t.state is TaskState.COMPLETED for t in session.graph.tasks)
    assert model.task_prompts[:3] == [1, 2, 3]  # TODO 1 → 2 → 3, automatically

    # --- many model/tool cycles, not one call per TODO ------------------------
    assert model.model_calls >= 10, model.model_calls
    tool_calls = sum(1 for kind, _ in events if kind == "call")
    assert tool_calls >= 8, tool_calls

    # --- the model claimed completion while the tests were failing, and the
    #     session refused to accept the claim (verification, then a repair) ---
    assert model.failures_seen >= 1
    assert session.recoveries >= 1, "the false claim must have been repaired"
    assert session.graph.tasks[2].attempts >= 1
    test_runs = [c for c in session.session_evidence.commands if c.test]
    assert any(not c.ok for c in test_runs), "the simulated failure must be real"
    assert test_runs[-1].ok, "the last test run must pass before completion"

    # --- the lifecycle of the run, as the session reported it -----------------
    kinds = [kind for kind, _ in session_events]
    assert kinds.count("task_start") == 3
    assert kinds.count("task_done") == 3
    assert "plan_ready" in kinds
    assert "final_verify" in kinds
    assert "final_ok" in kinds
    assert "complete" in kinds

    # --- the project on disk is the finished one ------------------------------
    assert (tmp_path / "todo_app.py").exists()
    assert (tmp_path / "cli.py").exists()
    assert (tmp_path / "test_todo_app.py").exists()
    assert ADD_NEW in (tmp_path / "todo_app.py").read_text(encoding="utf-8")
    assert set(session.state.changed_files) >= {
        "todo_app.py",
        "cli.py",
        "test_todo_app.py",
    }

    # --- each task keeps its own record, observed through the real agent -----
    final = session.graph.tasks[2]
    assert "todo_app.py" in final.inspected, final.inspected
    assert set(final.files_affected) >= {"test_todo_app.py"}
    assert final.tool_calls >= 3, final.tool_calls
    assert final.started_at and final.finished_at and final.duration_s >= 0
    assert final.verification
    assert any("pytest" in command for command in final.commands)
    assert final.current_action == ""
    # ...and the persistent context remembers what was inspected (§3).
    assert "todo_app.py" in session.state.inspected

    # The per-task record is what the session persists across a resume.
    record = state.store.load_plan()["tasks"][2]
    assert record["inspected"] and record["verification"] and record["finished_at"]

    # The independent check: the built project really passes its own suite.
    finished = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert finished.returncode == 0, finished.stdout + finished.stderr


def test_pipeline_through_the_cli_renders_compact_progress(tmp_path: Path, monkeypatch) -> None:
    """The whole user-facing path: app._handle_codemode + the compact task view."""
    monkeypatch.chdir(tmp_path)
    cms.enable(tmp_path)
    model = CodingModel()
    ui = _console_ui()
    presenter = app._TaskPresenter(ui)
    engine = _engine(tmp_path, model, ui=ui)

    class _FakeHistory:
        def __init__(self) -> None:
            self.saved = 0

        def save(self, transcript) -> None:
            self.saved += 1

    history = _FakeHistory()
    with lifecycle().task_span():
        app._handle_codemode(
            ui,
            engine,
            history,
            "Create a small todo application with a JSON store, validation, a CLI and tests.",
            presenter,
        )

    out = ui.console.export_text()
    # The compact header, the plan checklist, and real per-task progress.
    assert "SEEDCODE 8.1.0" in out
    assert "CODE MODE" in out
    assert "Task 3/3" in out
    assert "✓" in out
    assert "completed" in out.lower()
    assert "Project completed" in out
    assert "Ready for next task." in out
    # The CLI is still alive and at the prompt afterwards.
    assert lifecycle().phase.value == "idle"
    assert history.saved == 1
    # And the run really built the project.
    assert (tmp_path / "todo_app.py").exists()
    assert (tmp_path / "test_todo_app.py").exists()


def test_stopping_a_session_keeps_the_plan_and_offers_resume(tmp_path: Path, monkeypatch) -> None:
    """`/stop` (or Ctrl+C) ends the run safely and says it is resumable."""
    from seedcode.core.tasks import TurnEvidence

    monkeypatch.chdir(tmp_path)
    state = cms.enable(tmp_path)
    ui = _console_ui()

    class StoppingAgent:
        """Plans two tasks, finishes the first, then the user stops the run."""

        def __init__(self) -> None:
            self.messages: list = []
            self.transcript: list = []
            self.last_evidence = TurnEvidence()
            self.calls = 0

        def run_turn(self, text: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return PLAN_REPLY  # three tasks, the first is enough here
            # Task 1 really lands on disk, then the user stops the run.
            (tmp_path / "todo_app.py").write_text(STORE_V1, encoding="utf-8")
            self.last_evidence = TurnEvidence(
                changed_files=["todo_app.py"], tools=1
            )
            session_mod.stop()
            return "TASK 1 COMPLETE"

    class _FakeHistory:
        def save(self, transcript) -> None:
            pass

    agent = StoppingAgent()
    with lifecycle().task_span():
        app._handle_codemode(
            ui, agent, _FakeHistory(), "Build the todo app", app._TaskPresenter(ui)
        )

    out = ui.console.export_text()
    assert "stopped" in out
    assert "/resume" in out  # the session is resumable, not lost
    assert "Cancelled" in out or "cancelled" in out
    # The CLI is still alive and at the prompt afterwards.
    assert lifecycle().phase.value == "idle"

    # The plan and the finished task survived in the checkpoint.
    checkpoint = state.store.load_checkpoint()
    assert checkpoint is not None
    assert checkpoint["status"] == "cancelled"
    assert checkpoint["next_action"]
    assert any(
        task["state"] == "completed" for task in checkpoint["plan"]["tasks"]
    ), checkpoint["plan"]["tasks"]


def test_fixture_project_passes_once_the_fix_is_applied(tmp_path: Path) -> None:
    """A sanity check on the fixture itself, so the E2E above stays honest."""
    (tmp_path / "todo_app.py").write_text(STORE_V1.replace(ADD_OLD, ADD_NEW), encoding="utf-8")
    (tmp_path / "test_todo_app.py").write_text(TESTS, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # ...and it fails before the fix, which is what makes the E2E realistic.
    (tmp_path / "todo_app.py").write_text(STORE_V1, encoding="utf-8")
    failing = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert failing.returncode == 1
