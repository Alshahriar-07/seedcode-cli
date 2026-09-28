"""v9.1.0 Agent Mode execution: planning is not execution.

The reported defect: asking Agent Mode to "create index.html" (or "build a
website with HTML CSS JS") produced ``.seedcode`` bookkeeping, a plan and a
"Task 1/1 completed / Project completed / verification: accepted" banner — but
none of the requested files. Planning was being reported as execution.

Two independent guarantees are locked in here:

1. **Verification requires real evidence.** A task never verifies from a clean
   error log, a plan, or a sentence: it must have created/modified a file or
   run a command successfully, and every declared criterion is checked against
   the actual workspace.
2. **The agent really works.** Driven through the real tool registry, a
   website request writes ``index.html``/``style.css``/``script.js`` into the
   active workspace, runs commands there, verifies them, and only then reports
   completion. A model that only talks cannot reach "completed".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from seedcode import app, codemode_state as cms
from seedcode.core import session as session_mod
from seedcode.core.agent import AgentEngine
from seedcode.core.lifecycle import lifecycle
from seedcode.core.models import AppConfig
from seedcode.core.session import SessionStatus
from seedcode.core.tasks import (
    Task,
    TaskGraph,
    TaskState,
    TurnEvidence,
    verify_task,
)
from seedcode.tools import PermissionLevel, PermissionManager
from seedcode.ui import UI
from seedcode.ui.theme import SEED_THEME

PY = f'"{sys.executable}"'

INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Test Site</title>
    <link rel="stylesheet" href="style.css">
  </head>
  <body>
    <h1 id="title">Test Site</h1>
    <script src="script.js"></script>
  </body>
</html>
"""

STYLE_CSS = "body { font-family: sans-serif; }\n"
SCRIPT_JS = "document.getElementById('title').textContent = 'Loaded';\n"

SITE_PLAN = """\
I inspected the workspace (it is empty). Here is the plan.

```plan
{"tasks": [
  {"title": "Build the static site",
   "detail": "index.html linking style.css and script.js",
   "acceptance": ["file: index.html | contains: style.css",
                  "file: index.html | contains: script.js",
                  "file: style.css", "file: script.js"],
   "depends_on": []}
]}
```

Task 1 is next.
"""


# --- shared harness -----------------------------------------------------------
def _block(tool: str, args: dict) -> str:
    return "```tool\n" + json.dumps({"tool": tool, "args": args}) + "\n```"


def _console_ui(width: int = 110) -> UI:
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


class _FakeHistory:
    def __init__(self) -> None:
        self.saved = 0

    def save(self, transcript) -> None:
        self.saved += 1


@pytest.fixture(autouse=True)
def _clean_state():
    cms.reset()
    session_mod.reset()
    lifecycle().reset_for_tests()
    yield
    cms.reset()
    session_mod.reset()
    lifecycle().reset_for_tests()


def _tool_engine(workspace: Path, model) -> AgentEngine:
    """A real AgentEngine running the real tools, driven by ``model``."""
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)
    engine = AgentEngine(config, perm)
    engine._native = False  # text protocol: no provider needed, real tools

    def stream_reply():
        return iter([model.reply(engine)])

    engine.stream_reply = stream_reply  # type: ignore[method-assign]
    return engine


class TalkOnlyAgent:
    """A model that only ever describes the work — it never calls a tool.

    This is the exact failure mode that was reported: a plan is produced and
    the model talks confidently, but nothing is written.
    """

    def __init__(self, plan: str = SITE_PLAN) -> None:
        self.plan = plan
        self.messages: list = []
        self.transcript: list = []
        self.last_evidence = TurnEvidence()
        self.calls = 0

    def run_turn(self, text: str) -> str:
        self.calls += 1
        if "implementation plan for THIS repository" in text:
            return self.plan
        # Every subsequent turn: prose only, no tool blocks, no work done.
        return (
            "I have created index.html, style.css and script.js in the "
            "workspace. The site is complete and the files are connected."
        )


class SiteModel:
    """A deterministic model that really builds the site through the tools."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.tool_calls = 0
        self._wrote = False

    def reply(self, engine: AgentEngine) -> str:
        prompt = engine.messages[-1].content or ""
        on_tool_result = engine.messages[-1].role == "tool" or prompt.startswith(
            "[TOOL RESULTS]"
        )

        if "implementation plan for THIS repository" in prompt:
            return SITE_PLAN

        if "FINAL VERIFICATION" in prompt:
            if on_tool_result:
                return "FINAL VERIFICATION PASSED"
            return self._write_site() + "\n" + _block(
                "run_command", {"command": f'{PY} -c "import os; assert os.path.isfile(\'index.html\')"'}
            )

        if on_tool_result:
            return "TASK 1 COMPLETE"

        if not self._wrote:
            self._wrote = True
            return self._write_site() + "\n" + _block(
                "run_command",
                {"command": f'{PY} -c "print(open(\'index.html\').read()[:15])"'},
            )
        return "TASK 1 COMPLETE"

    def _write_site(self) -> str:
        self.tool_calls += 3
        return "\n".join(
            [
                _block("write_file", {"path": "index.html", "content": INDEX_HTML}),
                _block("write_file", {"path": "style.css", "content": STYLE_CSS}),
                _block("write_file", {"path": "script.js", "content": SCRIPT_JS}),
            ]
        )


class EditModel:
    """Writes a file, then edits it, proving modification is verified."""

    def __init__(self) -> None:
        self._written = False
        self._edited = False

    def reply(self, engine: AgentEngine) -> str:
        prompt = engine.messages[-1].content or ""
        on_tool_result = engine.messages[-1].role == "tool" or prompt.startswith(
            "[TOOL RESULTS]"
        )

        if "implementation plan for THIS repository" in prompt:
            return (
                "```plan\n"
                + json.dumps(
                    {
                        "tasks": [
                            {
                                "title": "Implement the fix",
                                "detail": "app.py must contain FIXED",
                                "acceptance": ["file: app.py | contains: FIXED"],
                                "depends_on": [],
                            }
                        ]
                    }
                )
                + "\n```\nTask 1 is next.\n"
            )

        if "FINAL VERIFICATION" in prompt:
            if on_tool_result:
                return "FINAL VERIFICATION PASSED"
            return _block("read_file", {"path": "app.py"})

        if on_tool_result:
            return "TASK 1 COMPLETE"

        if not self._edited:
            self._edited = True
            return _block("read_file", {"path": "app.py"}) + "\n" + _block(
                "edit_file",
                {"path": "app.py", "old_text": "value = 1", "new_text": "value = 1  # FIXED"},
            )
        return "TASK 1 COMPLETE"


def _run_session(workspace: Path, agent, store=None, **kwargs) -> session_mod.CodeSession:
    session = session_mod.CodeSession(
        agent,
        workspace=workspace,
        request="Create a simple website with HTML, CSS and JS.",
        store=store,
        sleep=lambda _s: None,
        **kwargs,
    )
    session.run()
    return session


# --- 1. verification requires real evidence -----------------------------------
class TestVerificationRequiresEvidence:
    def test_no_errors_alone_never_verifies(self) -> None:
        """The exact loophole: a clean error log is not proof of work."""
        task = Task(id=1, title="Create index.html", acceptance=["no-errors"])
        outcome = verify_task(task, TurnEvidence(), Path.cwd())
        assert not outcome.ok
        assert "no concrete progress" in outcome.detail

    def test_no_errors_plus_real_work_verifies(self) -> None:
        task = Task(id=1, title="Create index.html", acceptance=["no-errors"])
        evidence = TurnEvidence()
        evidence.note_file("index.html")
        assert verify_task(task, evidence, Path.cwd()).ok

    def test_a_declared_file_criterion_is_checked_on_disk(self, tmp_path: Path) -> None:
        task = Task(
            id=1, title="Create index.html", acceptance=["file: index.html"]
        )
        evidence = TurnEvidence()
        evidence.note_file("index.html")
        assert not verify_task(task, evidence, tmp_path).ok

        (tmp_path / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
        assert verify_task(task, evidence, tmp_path).ok

    def test_task_graph_never_completes_on_skips_or_claims(self) -> None:
        graph = TaskGraph.single("create index.html")
        graph.tasks[0].mark(TaskState.RUNNING)
        graph.tasks[0].mark(TaskState.VERIFYING)
        assert not graph.all_completed()

    def test_success_requires_observed_progress(self) -> None:
        evidence = TurnEvidence(tools=7, model_calls=3)
        assert not evidence.any_success
        evidence.note_command("python -c pass", True)
        assert evidence.any_success


# --- 2. talking is not doing --------------------------------------------------
class TestTalkOnlyAgentCannotComplete:
    def test_the_project_is_not_reported_complete(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        state = cms.enable(tmp_path)
        session = _run_session(tmp_path, TalkOnlyAgent(), store=state.store)

        assert session.status is not SessionStatus.COMPLETED
        assert session.status is SessionStatus.FAILED
        assert not session.graph.all_completed()
        assert all(
            t.state is not TaskState.COMPLETED for t in session.graph.tasks
        ), [t.state for t in session.graph.tasks]

    def test_no_files_were_created_and_nothing_pretends_otherwise(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        state = cms.enable(tmp_path)
        session = _run_session(tmp_path, TalkOnlyAgent(), store=state.store)

        assert not (tmp_path / "index.html").exists()
        assert not (tmp_path / "style.css").exists()
        assert session.state.changed_files == []
        assert "no file" in (session.reason or "").lower() or session.reason

    def test_the_banner_does_not_claim_success(self, tmp_path: Path, monkeypatch) -> None:
        """The user-facing path: no "Project completed" unless it really is."""
        monkeypatch.chdir(tmp_path)
        cms.enable(tmp_path)
        ui = _console_ui()
        with lifecycle().task_span():
            app._handle_codemode(
                ui,
                TalkOnlyAgent(),
                _FakeHistory(),
                "Create a simple website with HTML, CSS and JS.",
                app._TaskPresenter(ui),
            )
        out = ui.console.export_text()
        lowered = out.lower()
        # No success banner, no "verified" claim — only the real blocker.
        assert "Project completed" not in out
        assert "verification: accepted" not in lowered
        assert "blocked" in lowered or "failed" in lowered
        assert "index.html does not exist" in out
        assert not (tmp_path / "index.html").exists()


# --- 3. the agent really builds the site --------------------------------------
class TestWebsiteRequestReallyBuildsTheSite:
    def test_files_are_created_linked_and_verified(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        state = cms.enable(tmp_path)
        model = SiteModel(tmp_path)
        engine = _tool_engine(tmp_path, model)
        session = _run_session(tmp_path, engine, store=state.store)

        assert session.status is SessionStatus.COMPLETED, session.reason

        # The requested project really exists in the selected workspace...
        index = tmp_path / "index.html"
        style = tmp_path / "style.css"
        script = tmp_path / "script.js"
        assert index.is_file() and style.is_file() and script.is_file()
        # ...and it is connected the way a website must be.
        html = index.read_text(encoding="utf-8")
        assert 'href="style.css"' in html
        assert 'src="script.js"' in html
        assert "font-family" in style.read_text(encoding="utf-8")
        assert "getElementById" in script.read_text(encoding="utf-8")

        # Verification read the real files and recorded real evidence.
        assert set(session.state.changed_files) >= {
            "index.html",
            "style.css",
            "script.js",
        }
        task = session.graph.tasks[0]
        assert task.state is TaskState.COMPLETED
        assert task.verification
        assert set(task.files_affected) >= {"index.html", "style.css", "script.js"}
        assert any("index.html" in c for c in task.commands)

    def test_terminal_commands_run_inside_the_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        state = cms.enable(tmp_path)
        session = _run_session(tmp_path, _tool_engine(tmp_path, SiteModel(tmp_path)), store=state.store)

        commands = session.session_evidence.commands
        assert commands, "the agent must have run a command"
        assert all(c.ok for c in commands), [c.command for c in commands if not c.ok]

        # A command that writes a relative path lands in the workspace, and its
        # exit status is the evidence the task is verified with.
        config = AppConfig(provider="openrouter", model="test/model")
        config.set_api_key("openrouter", "sk-or-test")
        perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
        from seedcode.tools import get_tool

        result = get_tool("run_command").run(
            perm,
            {"command": f'{PY} -c "open(\'marker.txt\', \'w\').write(\'ok\')"'},
        )
        assert result.ok, result.output
        assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "ok"

    def test_a_modification_is_verified(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
        state = cms.enable(tmp_path)
        session = _run_session(tmp_path, _tool_engine(tmp_path, EditModel()), store=state.store)

        assert session.status is SessionStatus.COMPLETED, session.reason
        assert "FIXED" in (tmp_path / "app.py").read_text(encoding="utf-8")
        assert "app.py" in session.graph.tasks[0].files_affected
        assert "app.py" in session.state.inspected  # the read was real too


class TestFalselyClaimedCompletion:
    def test_a_claim_without_work_is_rejected(self, tmp_path: Path, monkeypatch) -> None:
        """`TASK 1 COMPLETE` with nothing done is not accepted as completion."""

        class ClaimingAgent(TalkOnlyAgent):
            def run_turn(self, text: str) -> str:
                self.calls += 1
                if "implementation plan for THIS repository" in text:
                    return SITE_PLAN
                return "TASK 1 COMPLETE"

        monkeypatch.chdir(tmp_path)
        state = cms.enable(tmp_path)
        session = _run_session(tmp_path, ClaimingAgent(), store=state.store)

        assert session.status is SessionStatus.FAILED
        assert session.graph.tasks[0].state is not TaskState.COMPLETED
        # It tried, got rejected, and gave an honest reason instead of success.
        assert session.graph.tasks[0].attempts >= 1
        assert session.reason
        assert not (tmp_path / "index.html").exists()

    def test_a_failed_command_blocks_completion(self, tmp_path: Path, monkeypatch) -> None:
        """A task whose only command fails can never be reported verified."""

        class FailingAgent(TalkOnlyAgent):
            def run_turn(self, text: str) -> str:
                self.calls += 1
                if "implementation plan for THIS repository" in text:
                    return (
                        "```plan\n"
                        + json.dumps(
                            {
                                "tasks": [
                                    {
                                        "title": "Run the build",
                                        "acceptance": ["run: bogus-build"],
                                        "depends_on": [],
                                    }
                                ]
                            }
                        )
                        + "\n```\nTask 1 is next.\n"
                    )
                self.last_evidence = TurnEvidence()
                self.last_evidence.note_command(
                    f"{PY} -c \"raise SystemExit(1)\"", False, "exit code 1"
                )
                self.last_evidence.note_error("run_command: FAILED (exit code 1)")
                return "TASK 1 COMPLETE"

        monkeypatch.chdir(tmp_path)
        state = cms.enable(tmp_path)
        session = _run_session(tmp_path, FailingAgent(), store=state.store)

        assert session.status is not SessionStatus.COMPLETED
        assert session.graph.tasks[0].state is not TaskState.COMPLETED
