"""v9.2.0 live activity, diff tracking, and directory deletion."""

from __future__ import annotations

from pathlib import Path

from seedcode.app import _TaskPresenter
from seedcode.core.events import build_event, event_type_for
from seedcode.tools import PermissionManager, get_tool
from seedcode.tools.diffs import diff_details, make_diff
from seedcode.ui.content import plain_text
from seedcode.ui.state import AppState
from seedcode.ui.tui import ChatTUI


def test_created_file_diff_reports_real_line_range() -> None:
    diff = make_diff("src/app.py", "", "one\ntwo\nthree\n", created=True)
    details = diff_details(diff)
    assert details["created"] is True
    assert details["added"] == 3 and details["removed"] == 0
    assert details["line"] == 1 and details["line_end"] == 3
    assert details["hunks"][0]["new_start"] == 1

    event = build_event(
        phase="tool_done",
        name="write_file",
        args={"path": "src/app.py"},
        ok=True,
        diff=diff,
        path="src/app.py",
    )
    assert event.type == "file_created"
    assert event.lines_added == 3 and event.lines_removed == 0
    assert event.line == 1 and event.line_end == 3
    assert event.hunks and "Created src/app.py" in event.message
    assert "+3" in event.message


def test_updated_file_diff_counts_added_and_removed_lines() -> None:
    diff = make_diff("a.txt", "one\ntwo\nthree\n", "one\nTWO\nthree\n")
    details = diff_details(diff)
    assert details["added"] == 1 and details["removed"] == 1
    assert details["line"] == 1 and details["line_end"] == 3

    event = build_event(
        phase="tool_done", name="edit_file", args={"path": "a.txt"}, ok=True,
        diff=diff, path="a.txt",
    )
    assert event.type == "file_updated"
    assert event.lines_added == 1 and event.lines_removed == 1
    assert "Updated a.txt" in event.message


def test_event_types_match_the_real_operation() -> None:
    assert event_type_for("tool_start", "run_command", None, {"command": "pytest -q"}) == "test_started"
    assert event_type_for("tool_done", "run_command", True, {"command": "pytest -q"}) == "test_completed"
    assert event_type_for("tool_done", "run_command", True, {"command": "npm run build"}) == "terminal_completed"
    assert event_type_for("tool_done", "web_search", True, {"query": "docs"}) == "internet_search"
    assert event_type_for("tool_done", "web_fetch", True, {"url": "https://example.com"}) == "internet_result"
    assert event_type_for("tool_done", "read_file", True, {"path": "a.py"}) == "file_read"
    assert event_type_for("tool_done", "project_index", True) == "project_scan"
    assert event_type_for("tool_done", "delete_directory", True, {"path": "pkg"}) == "folder_deleted"
    assert event_type_for("tool_done", "write_file", False, {"path": "a.py"}) == "error"
    assert event_type_for("terminal_output") == "terminal_output"


def test_tui_presenter_renders_each_event_once() -> None:
    class FakeUI:
        is_tui = True

        def __init__(self) -> None:
            self.activities: list[tuple[str, str]] = []
            self.changes: list[dict] = []

        def append_activity(self, kind: str, message: str) -> None:
            self.activities.append((kind, message))

        def append_change(self, event: dict) -> None:
            self.changes.append(event)

    ui = FakeUI()
    presenter = _TaskPresenter(ui)
    diff = make_diff("a.py", "", "x = 1\n", created=True)
    event = build_event(
        phase="tool_done", name="write_file", args={"path": "a.py"}, ok=True,
        diff=diff, path="a.py",
    )
    payload = event.to_dict()
    payload.update(
        {"phase": "tool_done", "name": "write_file", "args": {"path": "a.py"}, "ok": True}
    )
    presenter.on_step(payload)
    presenter.on_step(payload)  # same event id: must not duplicate

    # One operation, one line: the diff view owns the file-change summary, so
    # the generic event message is not printed on top of it.
    assert ui.activities == []
    assert len(ui.changes) == 1


def test_a_tool_without_a_diff_renders_one_activity_line() -> None:
    class FakeUI:
        is_tui = True

        def __init__(self) -> None:
            self.activities: list[tuple[str, str]] = []
            self.changes: list[dict] = []

        def append_activity(self, kind: str, message: str) -> None:
            self.activities.append((kind, message))

        def append_change(self, event: dict) -> None:
            self.changes.append(event)

    ui = FakeUI()
    presenter = _TaskPresenter(ui)
    event = build_event(
        phase="tool_done", name="read_file", args={"path": "a.py"}, ok=True,
        output="x = 1", path="a.py",
    )
    payload = event.to_dict()
    payload.update(
        {"phase": "tool_done", "name": "read_file", "args": {"path": "a.py"}, "ok": True}
    )
    presenter.on_step(payload)
    presenter.on_step(payload)

    assert len(ui.activities) == 1
    assert ui.activities[0][0] == "success"
    assert "Read a.py" in ui.activities[0][1]
    assert ui.changes == []


def test_tui_renders_diff_and_expands_it() -> None:
    tui = ChatTUI(AppState(), width=80, height=24)
    diff = make_diff("src/app.py", "", "def main():\n    return 1\n", created=True)
    event = build_event(
        phase="tool_done", name="write_file", args={"path": "src/app.py"}, ok=True,
        diff=diff, path="src/app.py",
    ).to_dict()
    tui.append_change(event)
    text = plain_text(tui.buffer.lines())
    assert "Created src/app.py" in text
    assert "+2" in text
    assert "def main():" in text
    assert "Ctrl+E" in text

    tui.show_last_change()
    expanded = plain_text(tui.buffer.lines())
    assert "Full diff for src/app.py" in expanded
    assert "def main():" in expanded


def test_delete_directory_requires_recursive_for_non_empty(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "pkg"
    target.mkdir()
    (target / "a.txt").write_text("x", encoding="utf-8")
    perms = PermissionManager(workspace=workspace)

    refused = get_tool("delete_directory").run(perms, {"path": "pkg"})
    assert refused.ok is False
    assert target.exists()

    deleted = get_tool("delete_directory").run(
        perms, {"path": "pkg", "recursive": "true"}
    )
    assert deleted.ok is True
    assert not target.exists()


def test_delete_directory_refuses_the_workspace_root(tmp_path: Path) -> None:
    perms = PermissionManager(workspace=tmp_path)
    result = get_tool("delete_directory").run(perms, {"path": ".", "recursive": True})
    assert result.ok is False
    assert tmp_path.exists()
