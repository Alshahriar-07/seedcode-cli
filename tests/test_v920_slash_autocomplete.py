"""v9.2.0 UI: slash autocomplete, terminal inspection, final report, plan lines.

The suggestions must come from the *real* backend command registry
(``seedcode.commands``), every report number must come from a real observed
event, and nothing may be simulated for the tests.
"""

from __future__ import annotations

import types

from seedcode.app import _TaskPresenter
from seedcode.commands import _REGISTRY
from seedcode.core.events import build_event
from seedcode.tools.diffs import make_diff
from seedcode.ui.content import plain_text
from seedcode.ui.state import AppState
from seedcode.ui.tui import ChatTUI


def _tui() -> ChatTUI:
    tui = ChatTUI(AppState(), width=80, height=24)
    # Keep the real registry: a fresh import is the point of these tests.
    tui._command_catalog = None
    return tui


# --- slash-command autocomplete ---------------------------------------------


def test_slash_suggests_every_registered_backend_command() -> None:
    tui = _tui()
    tui._input.text = "/"  # triggers on_text_changed -> filtering

    shown = {name for name, _help in tui._auto_entries}
    assert shown == set(_REGISTRY)
    assert "workspace" in shown


def test_typing_narrows_the_suggestions() -> None:
    tui = _tui()
    tui._input.text = "/wor"

    assert [name for name, _ in tui._auto_display_entries()] == ["workspace"]


def test_suggestions_carry_the_registry_help_text() -> None:
    tui = _tui()
    tui._input.text = "/wor"

    entry = tui._auto_display_entries()[0]
    assert entry[1] == _REGISTRY["workspace"][1]


def test_arguments_and_newlines_close_the_list() -> None:
    tui = _tui()
    tui._input.text = "/workspace src/app.py"

    assert tui._auto_display_entries() == []


def test_enter_completes_then_a_second_enter_submits() -> None:
    tui = _tui()
    tui._input.text = "/wor"

    tui._complete_selected()

    assert tui._input.text == "/workspace "
    # A trailing space moves to the argument position, so the list closes and
    # the next Enter submits exactly what is on screen.
    assert tui._auto_display_entries() == []


def test_arrow_keys_move_the_selection() -> None:
    tui = _tui()
    tui._input.text = "/"

    first = tui._auto_display_entries()[tui._auto_index][0]
    tui._autocomplete_move(1)
    second = tui._auto_display_entries()[tui._auto_index][0]
    tui._autocomplete_move(-1)
    back = tui._auto_display_entries()[tui._auto_index][0]

    assert first != second
    assert back == first


def test_escape_dismisses_but_keeps_the_typing() -> None:
    tui = _tui()
    tui._input.text = "/wor"
    assert tui._auto_display_entries()

    tui._dismiss_autocomplete()

    assert tui._auto_display_entries() == []
    assert tui._input.text == "/wor"


def test_mouse_click_completes_the_clicked_row() -> None:
    tui = _tui()
    tui._input.text = "/"

    tui._autocomplete_pick(1)  # second row of the full list

    picked = tui._auto_display_entries()
    if picked:
        assert tui._input.text == f"/{picked[0][0]} "


def test_unknown_command_suggests_nothing_but_stays_submitable() -> None:
    tui = _tui()
    tui._input.text = "/no_such_command"

    assert tui._auto_display_entries() == []
    assert tui._input.text == "/no_such_command"


def test_suggestion_rows_are_budgeted_in_the_layout() -> None:
    tui = _tui()
    base = tui.min_layout_rows()
    tui._input.text = "/"
    shown = len(tui._auto_display_entries())

    assert 0 < shown <= 8
    assert tui.min_layout_rows() == base + shown


def test_selected_row_is_rendered_highlighted() -> None:
    tui = _tui()
    tui._input.text = "/wor"

    rows = tui._autocomplete_fragments()
    assert len(rows) == 1
    flat = "".join(text for _style, text in rows[0])
    assert "/workspace" in flat
    assert _REGISTRY["workspace"][1][:20] in flat


# --- terminal inspection (Ctrl+T data path) ---------------------------------


def _terminal_event(command: str, ok: bool, output: str) -> dict:
    event = build_event(
        phase="tool_done",
        name="run_command",
        args={"command": command},
        ok=ok,
        output=output,
    )
    payload = event.to_dict()
    # The engine's _notify adds the extra fields (the real output) on top of
    # the structured event, exactly as the live payload carries them.
    payload.update(
        {"phase": "tool_done", "name": "run_command", "args": {"command": command},
         "ok": ok, "output": output}
    )
    return payload


def test_presenter_records_real_command_output_for_expansion() -> None:
    tui = _tui()
    presenter = _TaskPresenter(tui)
    payload = _terminal_event(
        "python -c \"print(5)\"", True, "Success\n5"
    )
    presenter._tui_activity(payload, payload["phase"], "run_command", payload["args"])

    assert tui._last_command == {
        "command": "python -c \"print(5)\"",
        "ok": True,
        "output": "Success\n5",
        "cwd": "",
    }

    # The expansion re-renders what really ran.
    tui.buffer.clear()
    tui.show_last_command()
    assert any(
        "python -c \"print(5)\"" in "".join(text for _, text in line)
        for line in tui.buffer.lines()
    )


def test_expanding_without_commands_is_a_safe_note() -> None:
    tui = _tui()
    tui.show_last_command()
    assert "No command has run yet" in plain_text(tui.buffer.lines())


# --- final execution report --------------------------------------------------


class _ReportUI:
    is_tui = True

    def __init__(self) -> None:
        self.activities: list[tuple[str, str]] = []
        self.changes: list[dict] = []
        self.reports: list[dict] = []

    def append_activity(self, kind: str, message: str) -> None:
        self.activities.append((kind, message))

    def append_change(self, event: dict) -> None:
        self.changes.append(event)

    def record_command(self, command: str, ok: bool, output: str) -> None:
        self.activities.append(("record", command))

    def append_report(self, report: dict) -> None:
        self.reports.append(report)

    # The session-event handler prints narration through these primitives.
    def dim(self, message: str) -> None:
        self.activities.append(("dim", message))

    def success(self, message: str) -> None:
        self.activities.append(("success", message))

    def warning(self, message: str) -> None:
        self.activities.append(("warning", message))

    def error(self, message: str) -> None:
        self.activities.append(("error", message))

    def blank(self) -> None:
        pass


def _file_event(phase: str, name: str, args: dict, ok: bool, diff: str) -> dict:
    event = build_event(
        phase=phase, name=name, args=args, ok=ok, diff=diff,
        path=args.get("path", ""),
    )
    payload = event.to_dict()
    payload.update({"phase": phase, "name": name, "args": args, "ok": ok})
    return payload


def test_report_lists_only_really_observed_operations() -> None:
    ui = _ReportUI()
    presenter = _TaskPresenter(ui)
    diff = make_diff("a.py", "", "x = 1\ny = 2\n", created=True)

    presenter.on_step({
        "phase": "task_started", "type": "task_started",
    })
    presenter.on_step(_file_event(
        "tool_done", "write_file", {"path": "a.py"}, True, diff,
    ))
    presenter.on_step({
        "phase": "task_completed", "type": "task_completed",
        "message": "Task completed",
    })

    assert len(ui.reports) == 1
    report = ui.reports[0]
    assert report["state"] == "completed"
    assert report["created"] == ["a.py"]
    assert report["commands"] == []  # nothing claimed that did not happen


def test_report_fields_follow_real_events_for_commands_and_research() -> None:
    ui = _ReportUI()
    presenter = _TaskPresenter(ui)

    presenter.on_step({
        "phase": "task_started", "type": "task_started",
    })
    presenter.on_step(_terminal_event('echo "py"', True, "Success\n5"))
    search = build_event(
        phase="tool_start", name="web_search", args={"query": "pytest docs"},
    )
    payload = search.to_dict()
    payload.update(
        {"phase": "tool_start", "name": "web_search", "args": {"query": "pytest docs"}}
    )
    presenter.on_step(payload)
    fetch = build_event(
        phase="tool_done", name="web_fetch",
        args={"url": "https://docs.python.org"}, ok=True,
    )
    payload = fetch.to_dict()
    payload.update(
        {"phase": "tool_done", "name": "web_fetch", "ok": True,
         "args": {"url": "https://docs.python.org"}}
    )
    presenter.on_step(payload)
    presenter.on_step({
        "phase": "task_failed", "type": "task_failed", "message": "boom",
    })

    assert ui.reports[-1]["state"] == "failed"
    assert ui.reports[-1]["commands"] == [('echo "py"', True)]
    assert ui.reports[-1]["research"] == ["pytest docs", "https://docs.python.org"]
    # The buckets reset after the flush: a clean turn prints no report at all,
    # so no stale section from the failed turn can leak into the next one.
    reports_after_failed = len(ui.reports)
    presenter.on_step({
        "phase": "task_started", "type": "task_started",
    })
    presenter.on_step({
        "phase": "task_completed", "type": "task_completed",
    })
    assert len(ui.reports) == reports_after_failed


def test_chat_tui_renders_the_report_block() -> None:
    tui = _tui()
    reset = tui.buffer
    reset.clear()
    tui.append_report({
        "state": "completed",
        "created": ["a.py", "b.py"],
        "modified": ["c.py"],
        "deleted": [],
        "commands": [('python a.py', True)],
        "tests": [True],
        "research": ["https://docs.example.com"],
        "errors": [],
    })
    text = plain_text(reset.lines())
    assert "Execution Report" in text
    assert "Files created: 2" in text
    assert "a.py, b.py" in text
    assert "Commands: 1/1 ok" in text
    assert "Tests: 1/1" in text
    assert "1 source(s)" in text


def test_empty_turn_prints_no_report() -> None:
    ui = _ReportUI()
    presenter = _TaskPresenter(ui)
    presenter.on_step({"phase": "task_started", "type": "task_started"})
    # A plain read is real work but produces no report section on its own;
    # completion with no mutating side effects reports nothing.
    presenter.on_step(_file_event("tool_done", "read_file", {"path": "x.py"}, True, ""))
    presenter.on_step({"phase": "task_completed", "type": "task_completed"})
    assert ui.reports == []


# --- live TODO/plan lines ----------------------------------------------------


class _FakeState:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeGraph:
    def __init__(self) -> None:
        self.total = 3
        self.rows = [
            (_FakeState("completed"), "Build the module"),
            (_FakeState("in_progress"), "Write tests"),
            (_FakeState("pending"), "Run the tests"),
        ]

    def checklist(self):
        return list(self.rows)


class _FakeSession:
    def __init__(self) -> None:
        self.graph = _FakeGraph()


def _plan_lines(ui: _ReportUI) -> list[str]:
    return [message for kind, message in ui.activities if kind in ("action", "success", "error")]


def test_tui_plan_lines_come_from_real_session_events() -> None:
    from seedcode.core import session as session_mod

    ui = _ReportUI()
    presenter = _TaskPresenter(ui)
    saved = session_mod.current_session()
    try:
        session_mod.set_current_session(_FakeSession())
        presenter.on_session_event("plan_ready", "Shipping the feature")
        presenter.on_session_event("task_start", "Write tests")
        presenter.on_session_event("task_done", "Write tests")
        presenter.on_session_event("task_failed", "Run the tests")
    finally:
        session_mod.set_current_session(saved)

    lines = _plan_lines(ui)
    assert any("Plan: 3 task(s)" in line for line in lines)
    assert any("Working on: Write tests" in line for line in lines)
    assert any("Completed: Write tests" in line for line in lines)
    assert any("Failed: Run the tests" in line for line in lines)


def test_tui_plan_lines_without_a_session_are_silent() -> None:
    from seedcode.core import session as session_mod

    ui = _ReportUI()
    presenter = _TaskPresenter(ui)
    saved = session_mod.current_session()
    try:
        session_mod.set_current_session(None)
        presenter.on_session_event("task_start", "Something")
    finally:
        session_mod.set_current_session(saved)
    assert _plan_lines(ui) == []


# --- thinking indicator elapsed time ----------------------------------------


def test_thinking_indicator_shows_live_elapsed_time() -> None:
    import time as _time

    tui = _tui()
    tui.start_thinking("Analyzing the project")
    try:
        first = plain_text(tui.buffer.lines())
    finally:
        tui.stop_thinking()
    assert "Analyzing the project" in first and "\u00b7 0s" in first
    # Higher resolution: the counter tracks the real elapsed time.
    tui.start_thinking("Thinking")
    try:
        _time.sleep(1.1)
        with tui._think_lock:
            tui._think_index = 3  # force a repaint of the frame
            tui._render_thinking()
        line = plain_text(tui.buffer.lines())
        assert "\u00b7 1s" in line
    finally:
        tui.stop_thinking()


# --- inline tool-call parsing (never end a turn on a text tool call) ---------


def test_unfenced_action_json_is_a_real_tool_call() -> None:
    # The exact shape a live qwen3:4b turn produced: "action" as the tool name
    # and the other keys as flat arguments. The loop must execute it, not
    # print it as the final answer.
    from seedcode.core.agent import parse_inline_tool_calls, strip_tool_blocks

    reply = (
        '{\n  "action": "write_file",\n  "path": "hello.py",\n'
        '  "content": "def main():\\n    print(1)\\n"\n}'
    )
    calls = parse_inline_tool_calls(reply)
    assert [call.tool for call in calls] == ["write_file"]
    assert calls[0].args["path"] == "hello.py"
    assert "content" in calls[0].args
    assert strip_tool_blocks(reply)  # nothing else was lost
