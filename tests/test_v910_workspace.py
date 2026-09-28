"""v9.1.0 workspace selection: the folder Seed Code is allowed to work in.

Before this release Seed Code silently treated wherever the terminal happened
to be (commonly ``C:\\Users\\<name>``) as the project. v9.1.0 asks, once per
start:

    Select Workspace

      1  Current Folder    <cwd>
      2  Choose a Folder   browse this PC and pick any directory

and makes the answer the single root for Agent Mode, file reads/writes,
project indexing, ``.seedcode``/``plan.json``, terminal commands and
verification. These tests exercise the real selection flow (with an injected
picker, so no GUI is needed), the ``/workspace`` command, and the
workspace-scoping of agent file operations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedcode import workspace as ws
from seedcode.commands import CommandContext, dispatch
from seedcode.commands import codemode as codemode_cmd
from seedcode.core.agent import AgentEngine
from seedcode.core.models import AppConfig
from seedcode.tools import PermissionLevel, PermissionManager


# --- stubs --------------------------------------------------------------------
class _RecordingUI:
    """Captures what a command would print (no terminal needed)."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def _emit(self, kind: str, text: str) -> None:
        self.lines.append(f"{kind}: {text}")

    def info(self, text: str) -> None:
        self._emit("info", text)

    def success(self, text: str) -> None:
        self._emit("success", text)

    def warning(self, text: str) -> None:
        self._emit("warning", text)

    def dim(self, text: str) -> None:
        self._emit("dim", text)

    def error(self, text: str) -> None:
        self._emit("error", text)

    def panel(self, renderable, title: str = "") -> None:
        self._emit("panel", title)

    def text(self) -> str:
        return "\n".join(self.lines)

    def clear(self) -> None:
        self.lines.clear()


class _FakePicker:
    def __init__(self, result: Path | None) -> None:
        self.result = result
        self.calls = 0

    def __call__(self, *_args, **_kwargs) -> Path | None:
        self.calls += 1
        return self.result


def _answers(values: list[str]):
    """An ``input`` stand-in that returns the scripted answers in order."""
    queue = list(values)

    def _input(_prompt: str = "") -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return _input


def _collect() -> tuple[list[str], object]:
    out: list[str] = []
    return out, out.append


# --- pure selection logic -----------------------------------------------------
class TestChoices:
    def test_empty_answer_is_the_current_folder(self) -> None:
        assert ws.parse_choice("", Path.cwd()) is ws.CURRENT
        assert ws.parse_choice("   ", Path.cwd()) is ws.CURRENT
        assert ws.parse_choice("1", Path.cwd()) is ws.CURRENT
        assert ws.parse_choice("current", Path.cwd()) is ws.CURRENT

    def test_two_is_choose_a_folder(self) -> None:
        for answer in ("2", "choose", "browse", "pick"):
            assert ws.parse_choice(answer, Path.cwd()) is ws.CHOOSE

    def test_an_existing_directory_path_is_accepted(self, tmp_path: Path) -> None:
        assert ws.parse_choice(str(tmp_path), Path.cwd()) == str(tmp_path)

    def test_a_non_directory_is_rejected(self, tmp_path: Path) -> None:
        assert ws.parse_choice(str(tmp_path / "nope"), Path.cwd()) is None
        assert ws.parse_choice("not a choice", Path.cwd()) is None

    def test_menu_offers_both_sides_and_shows_the_launch_directory(
        self, tmp_path: Path
    ) -> None:
        text = "\n".join(ws.menu_lines(tmp_path))
        assert "Select Workspace" in text
        assert "Current Folder" in text
        assert "Choose a Folder" in text
        assert str(tmp_path) in text


class TestSelectWorkspace:
    def test_current_folder_is_returned_unchanged(self, tmp_path: Path) -> None:
        out, print_fn = _collect()
        chosen = ws.select_workspace(
            cwd=tmp_path, input_fn=_answers([""]), print_fn=print_fn,
            picker=_FakePicker(None),
        )
        assert chosen == tmp_path
        assert "Keeping the current folder" not in "\n".join(out)

    def test_choose_a_folder_uses_the_native_picker(self, tmp_path: Path) -> None:
        target = tmp_path / "picked"
        target.mkdir()
        picker = _FakePicker(target)
        _, print_fn = _collect()
        chosen = ws.select_workspace(
            cwd=tmp_path, input_fn=_answers(["2"]), print_fn=print_fn, picker=picker
        )
        assert chosen == target
        assert picker.calls == 1

    def test_choose_a_folder_falls_back_to_a_typed_path(self, tmp_path: Path) -> None:
        target = tmp_path / "typed"
        target.mkdir()
        out, print_fn = _collect()
        chosen = ws.select_workspace(
            cwd=tmp_path,
            input_fn=_answers(["2", str(target)]),
            print_fn=print_fn,
            picker=_FakePicker(None),  # no GUI picker on this host
        )
        assert chosen == target
        assert "No folder picker is available" in "\n".join(out)

    def test_an_invalid_answer_re_prompts_then_accepts(self, tmp_path: Path) -> None:
        out, print_fn = _collect()
        chosen = ws.select_workspace(
            cwd=tmp_path,
            input_fn=_answers(["banana", "1"]),
            print_fn=print_fn,
            picker=_FakePicker(None),
        )
        assert chosen == tmp_path
        assert "Please answer" in "\n".join(out)

    def test_eof_keeps_the_current_folder(self, tmp_path: Path) -> None:
        out, print_fn = _collect()
        chosen = ws.select_workspace(
            cwd=tmp_path, input_fn=_answers([]), print_fn=print_fn,
            picker=_FakePicker(None),
        )
        assert chosen == tmp_path
        assert "Keeping the current folder" in "\n".join(out)

    def test_ctrl_c_keeps_the_current_folder(self, tmp_path: Path) -> None:
        def interrupting(_prompt: str = "") -> str:
            raise KeyboardInterrupt

        _, print_fn = _collect()
        chosen = ws.select_workspace(
            cwd=tmp_path, input_fn=interrupting, print_fn=print_fn,
            picker=_FakePicker(None),
        )
        assert chosen == tmp_path


class TestSetWorkspace:
    def test_set_and_reject(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "project"
        target.mkdir()
        monkeypatch.chdir(tmp_path)
        assert ws.set_workspace(target) == target.resolve()
        assert ws.active_workspace() == target.resolve()
        with pytest.raises(NotADirectoryError):
            ws.set_workspace(tmp_path / "missing")

    def test_label_is_the_folder_name(self, tmp_path: Path) -> None:
        target = tmp_path / "my-website"
        target.mkdir()
        assert ws.workspace_label(target) == "my-website"


class TestEnsureWorkspace:
    def test_explicit_env_selects_the_workspace(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "explicit"
        target.mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(ws.WORKSPACE_ENV, str(target))
        _, print_fn = _collect()
        assert ws.ensure_workspace(
            interactive=True, print_fn=print_fn, input_fn=_answers(["2"]),
            picker=_FakePicker(tmp_path / "other"),
        ) == target.resolve()
        assert Path.cwd() == target.resolve()

    def test_bad_env_is_reported_and_not_used(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(ws.WORKSPACE_ENV, str(tmp_path / "nope"))
        monkeypatch.setenv(ws.NO_PROMPT_ENV, "1")
        out, print_fn = _collect()
        ws.ensure_workspace(interactive=True, print_fn=print_fn)
        assert "was ignored" in "\n".join(out)
        assert Path.cwd() == tmp_path

    def test_non_interactive_never_prompts(self, tmp_path: Path, monkeypatch) -> None:
        """Pipelines/CI keep the launch directory instead of blocking."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv(ws.WORKSPACE_ENV, raising=False)
        calls: list[str] = []

        def input_fn(prompt: str = "") -> str:
            calls.append(prompt)
            return ""

        _, print_fn = _collect()
        result = ws.ensure_workspace(
            interactive=False, print_fn=print_fn, input_fn=input_fn
        )
        assert result == tmp_path
        assert calls == [], "a non-interactive host must not be prompted"

    def test_no_prompt_env_is_honoured(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(ws.NO_PROMPT_ENV, "1")
        _, print_fn = _collect()
        assert ws.ensure_workspace(
            interactive=True, print_fn=print_fn, input_fn=_answers(["2"])
        ) == tmp_path

    def test_interactive_prompt_selects_a_folder(self, tmp_path: Path, monkeypatch) -> None:
        launch = tmp_path / "home"
        launch.mkdir()
        project = tmp_path / "my-website"
        project.mkdir()
        monkeypatch.chdir(launch)
        monkeypatch.delenv(ws.WORKSPACE_ENV, raising=False)
        monkeypatch.delenv(ws.NO_PROMPT_ENV, raising=False)
        out, print_fn = _collect()
        result = ws.ensure_workspace(
            interactive=True,
            print_fn=print_fn,
            input_fn=_answers(["2"]),
            picker=_FakePicker(project),
        )
        assert result == project.resolve()
        assert Path.cwd() == project.resolve()
        assert "Select Workspace" in "\n".join(out)

    def test_launched_in_home_does_not_silently_become_the_project(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The reported confusion: ``C:\\Users\\...`` must not be assumed."""
        home = tmp_path / "Users" / "alsha"
        home.mkdir(parents=True)
        project = tmp_path / "Projects" / "my-website"
        project.mkdir(parents=True)
        monkeypatch.chdir(home)
        monkeypatch.delenv(ws.WORKSPACE_ENV, raising=False)
        _, print_fn = _collect()
        chosen = ws.ensure_workspace(
            interactive=True,
            print_fn=print_fn,
            input_fn=_answers(["2"]),
            picker=_FakePicker(project),
        )
        assert chosen == project.resolve()
        assert ws.active_workspace() == project.resolve()


# --- the workspace really controls everything --------------------------------
class TestWorkspaceControls:
    def test_seedcode_and_plan_land_in_the_selected_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = tmp_path / "site"
        project.mkdir()
        monkeypatch.chdir(project)

        from seedcode import codemode_state as cms

        cms.reset()
        try:
            state = cms.enable(ws.set_workspace(project))
            state.store.save_plan({"request": "build a site", "tasks": []})

            assert (project / ".seedcode").is_dir()
            assert (project / ".seedcode" / "plan.json").is_file()
            # Nothing was written outside the workspace.
            assert not (tmp_path / ".seedcode").exists()
        finally:
            cms.reset()

    def test_agent_file_operations_stay_inside_the_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        project = tmp_path / "TestSite"
        project.mkdir()
        monkeypatch.chdir(project)

        config = AppConfig(provider="openrouter", model="test/model")
        config.set_api_key("openrouter", "sk-or-test")
        perm = PermissionManager(workspace=ws.active_workspace(), level=PermissionLevel.WORKSPACE)
        engine = AgentEngine(config, perm)

        from seedcode.tools import get_tool

        inside = get_tool("write_file").run(perm, {"path": "index.html", "content": "<h1>hi</h1>"})
        assert inside.ok
        assert (project / "index.html").read_text(encoding="utf-8") == "<h1>hi</h1>"
        assert engine.permissions.workspace == project.resolve()

        # The permission layer refuses a write outside the workspace; through
        # the agent loop this becomes a failed tool result, never a silent write.
        from seedcode.tools import PermissionError_

        with pytest.raises(PermissionError_) as excinfo:
            get_tool("write_file").run(
                perm, {"path": str(outside / "sneaky.txt"), "content": "nope"}
            )
        assert "outside the workspace" in str(excinfo.value)
        assert not (outside / "sneaky.txt").exists()


# --- the /workspace command ---------------------------------------------------
class TestWorkspaceCommand:
    def _ctx(self, monkeypatch, ui: _RecordingUI) -> CommandContext:
        config = AppConfig(provider="openrouter", model="test/model")
        return CommandContext(ui=ui, config=config, engine=None)

    def test_show_reports_the_active_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        ui = _RecordingUI()
        ctx = self._ctx(monkeypatch, ui)
        dispatch(ctx, "/workspace")
        assert str(tmp_path) in ui.text()
        assert "/workspace change" in ui.text()

    def test_switching_reports_both_folders_and_moves_the_process(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        start = tmp_path / "start"
        target = tmp_path / "D" / "Projects" / "TestSite"
        start.mkdir()
        target.mkdir(parents=True)
        monkeypatch.chdir(start)
        ui = _RecordingUI()
        ctx = self._ctx(monkeypatch, ui)
        dispatch(ctx, f"/workspace {target}")
        assert Path.cwd() == target.resolve()
        text = ui.text()
        assert "Workspace changed" in text
        assert str(start.resolve()) in text and str(target.resolve()) in text

    def test_an_invalid_path_is_refused_without_moving(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        ui = _RecordingUI()
        ctx = self._ctx(monkeypatch, ui)
        dispatch(ctx, f"/workspace {tmp_path / 'nope'}")
        assert Path.cwd() == tmp_path
        assert "Command Error" in ui.text()

    def test_change_uses_the_picker(self, tmp_path: Path, monkeypatch) -> None:
        start = tmp_path / "start"
        target = tmp_path / "picked"
        start.mkdir()
        target.mkdir()
        monkeypatch.chdir(start)
        monkeypatch.setattr(ws, "pick_folder_native", lambda *a, **k: target)
        ui = _RecordingUI()
        ctx = self._ctx(monkeypatch, ui)
        dispatch(ctx, "/workspace change")
        assert Path.cwd() == target.resolve()

    def test_change_without_a_picker_says_how_to_do_it_manually(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(ws, "pick_folder_native", lambda *a, **k: None)
        ui = _RecordingUI()
        ctx = self._ctx(monkeypatch, ui)
        dispatch(ctx, "/workspace change")
        assert Path.cwd() == tmp_path
        assert "/workspace <path>" in ui.text()


# --- documentation ------------------------------------------------------------
class TestDocumentationCoversTheWorkspaceFlow:
    """The startup flow is user-facing, so the docs must describe it."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_readme_documents_the_select_workspace_prompt(self) -> None:
        readme = (self.ROOT / "README.md").read_text(encoding="utf-8")
        assert "Select Workspace" in readme
        assert "Current Folder" in readme
        assert "Choose a Folder" in readme
        # The scripted escapes are documented too, not just the prompt.
        assert ws.WORKSPACE_ENV in readme
        assert ws.NO_PROMPT_ENV in readme

    def test_release_notes_describe_the_startup_selection(self) -> None:
        notes = (self.ROOT / "RELEASE.md").read_text(encoding="utf-8")
        assert "Select Workspace" in notes
        assert "Choose a" in notes

    def test_changelog_has_an_entry_for_this_release(self) -> None:
        from seedcode import __version__

        changelog = (self.ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"## [{__version__}]" in changelog
