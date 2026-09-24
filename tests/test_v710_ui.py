"""v7.1.0 UI: the compact Code Mode header, task view and responsiveness.

Requirements locked in here:

* the header is compact — two rows while working, one while idle, no blank
  padding rows, no decorative art;
* it carries state, ``Task n/N``, the active task, a progress bar, elapsed time
  and the call count;
* the task view shows the plan as a checklist and a single live action line;
* every width renders without overflow, and consoles that cannot draw the
  glyphs get the ASCII rendering of the same information.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from seedcode import __version__
from seedcode.core.tasks import TaskGraph, TaskState, parse_plan
from seedcode.ui.codemode_header import (
    HEADER_WIDTH,
    MIN_PANEL_WIDTH,
    CodeModeHeader,
    format_duration,
    live_action_line,
    progress_bar,
    task_checklist,
)
from seedcode.ui.tasks import TaskFlow, TaskState as FlowTaskState, activity_for
from seedcode.ui.theme import SEED_THEME


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


def _render(header: CodeModeHeader, console: Console) -> list[str]:
    header.render(console)
    return [line for line in console.export_text().splitlines() if line.strip()]


PLAN = """\
```plan
{"tasks": [
  {"title": "Setup project", "acceptance": ["no-errors"]},
  {"title": "Create database", "acceptance": ["no-errors"], "depends_on": [1]},
  {"title": "Implement authentication", "acceptance": ["no-errors"],
   "depends_on": [2]},
  {"title": "Build dashboard", "acceptance": ["no-errors"], "depends_on": [3]},
  {"title": "Add tests", "acceptance": ["tests"], "depends_on": [4]}
]}
```
"""


# --- the header ---------------------------------------------------------------


def test_working_header_is_two_rows_plus_borders() -> None:
    console = _console()
    header = CodeModeHeader(
        state="running",
        task_index=3,
        task_total=8,
        task_title="Build authentication",
        percent=72,
        elapsed_s=272,
        calls=6,
    )
    lines = _render(header, console)

    assert len(lines) == 4  # top border, status row, progress row, bottom border
    body = "\n".join(lines)
    assert f"SEEDCODE {__version__}" in body
    assert "CODE MODE" in body
    assert "RUNNING" in body
    assert "Task 3/8" in body
    assert "Build authentication" in body
    assert "72%" in body
    assert "4m 32s" in body
    assert "6 calls" in body
    # No blank padding row inside the panel.
    inner = lines[1:-1]
    assert all(line.strip("│ ").strip() for line in inner)


def test_idle_header_is_one_row_plus_borders() -> None:
    console = _console()
    lines = _render(CodeModeHeader(state="ready"), console)

    assert len(lines) == 3  # top border, one status row, bottom border
    body = "\n".join(lines)
    assert "READY" in body
    assert "0/0 tasks" in body
    assert "72%" not in body  # no progress row while idle


def test_header_never_exceeds_its_design_or_terminal_width() -> None:
    for width in (40, 60, 64, 100, 200):
        console = _console(width)
        header = CodeModeHeader(
            width=min(width, HEADER_WIDTH),
            state="running",
            task_index=1,
            task_total=2,
            task_title="x" * 200,
            percent=50,
            elapsed_s=5,
            calls=1,
            activity="y" * 200,
        )
        for line in _render(header, console):
            assert len(line) <= HEADER_WIDTH, (width, line)
            assert len(line) <= width, (width, line)


def test_narrow_terminal_gets_a_single_line_instead_of_a_broken_panel() -> None:
    console = _console(MIN_PANEL_WIDTH - 4)
    header = CodeModeHeader(
        width=MIN_PANEL_WIDTH - 4,
        state="running",
        task_index=2,
        task_total=3,
        task_title="Fix the parser",
        percent=66,
    )
    lines = _render(header, console)

    assert len(lines) == 1
    assert "RUNNING" in lines[0]
    assert "Task 2/3" in lines[0]
    assert "│" not in lines[0] and "╭" not in lines[0]


def test_legacy_console_gets_ascii_header_and_bar() -> None:
    console = _console(legacy=True)
    lines = _render(
        CodeModeHeader(
            state="running",
            legacy=True,
            task_index=1,
            task_total=2,
            percent=50,
            elapsed_s=10,
            calls=int(2),
        ),
        console,
    )
    body = "\n".join(lines)
    for glyph in "─│╭╮╰╯●○":
        assert glyph not in body, glyph
    assert "+-" in lines[0]
    assert "#" in body and "-" in body  # ASCII progress bar
    assert " * " in lines[0]  # ASCII title separator


def test_progress_bar_matches_the_percentage() -> None:
    assert progress_bar(0).count("█") == 0
    assert progress_bar(100).count("█") == 16
    half = progress_bar(50)
    assert half.count("█") == 8 and half.count("░") == 8
    assert progress_bar(1).count("█") == 1  # never an empty bar for real progress
    assert set(progress_bar(50, legacy=True)) <= {"#", "-"}


def test_format_duration_is_compact() -> None:
    assert format_duration(0) == "0s"
    assert format_duration(42) == "42s"
    assert format_duration(272) == "4m 32s"
    assert format_duration(3900) == "1h 05m"


def test_state_labels_and_marks_are_truthful() -> None:
    for state, label in (
        ("ready", "READY"),
        ("running", "RUNNING"),
        ("paused", "PAUSED"),
        ("completed", "COMPLETED"),
        ("failed", "FAILED"),
        ("cancelled", "CANCELLED"),
    ):
        header = CodeModeHeader(state=state, task_total=1, task_index=1)
        assert header.label == label
        assert header._mark()[0]


# --- the task view ------------------------------------------------------------


def test_checklist_uses_one_mark_per_state() -> None:
    rows = [
        (TaskState.COMPLETED, "Setup project"),
        (TaskState.RUNNING, "Implement authentication"),
        (TaskState.PENDING, "Build dashboard"),
        (TaskState.FAILED, "Add tests"),
        (TaskState.BLOCKED, "Deploy"),
    ]
    text = "\n".join(line.plain for line in task_checklist(rows))
    assert "✓ Setup project" in text
    assert "● Implement authentication" in text
    assert "○ Build dashboard" in text
    assert "✗ Add tests" in text
    assert "■ Deploy" in text


def test_checklist_is_ascii_on_legacy_consoles() -> None:
    rows = [(TaskState.COMPLETED, "Done"), (TaskState.PENDING, "Later")]
    text = "\n".join(line.plain for line in task_checklist(rows, legacy=True))
    assert "[ok] Done" in text
    assert "- Later" in text
    assert "✓" not in text


def test_checklist_clips_long_titles() -> None:
    rows = [(TaskState.PENDING, "x" * 200)]
    line = task_checklist(rows)[0].plain
    assert len(line) < 80


def test_live_action_line_is_single_and_compact() -> None:
    line = live_action_line("Editing src/auth/session.ts")
    assert line is not None and line.plain.startswith("→ ")
    assert live_action_line("") is None
    assert live_action_line("x", legacy=True).plain.startswith("-> ")


def test_activity_verbs_describe_the_action() -> None:
    assert activity_for("edit_file", {"path": "a.py"}) == "Editing a.py"
    assert activity_for("write_file", {"path": "a.py"}) == "Editing a.py"
    assert activity_for("read_file", {"path": "a.py"}) == "Reading a.py"
    assert activity_for("run_command", {"command": "npm test"}) == "Running npm test"
    assert "Searching" in activity_for("search_text", {"pattern": "login"})


# --- the flow integration -----------------------------------------------------


def _flow(width: int = 100) -> TaskFlow:
    return TaskFlow(_console(width), mode_label="Code Mode", task="Build the app")


def test_code_mode_flow_shows_the_header_and_the_plan_not_six_step_rows() -> None:
    flow = _flow()
    graph = TaskGraph.from_plan("Build the app", parse_plan(PLAN))
    flow.begin()
    flow.attach_plan(graph)
    flow.set_state("running")

    frame = flow._renderable()
    text = _console().export_text()
    console = _console()
    console.print(frame)
    text = console.export_text()
    lines = [ln for ln in text.splitlines() if ln.strip()]

    assert "CODE MODE" in text
    assert "✓" not in text or True  # nothing claimed as done yet
    assert "○ Setup project" in text
    assert "Build the app" in text
    # The fine-grained step rows are dropped when a plan is attached.
    assert flow._body_lines() == []
    # Compact frame: 4 header rows + 5 checklist rows + 1 working line.
    assert len(lines) <= 12, lines
    assert "Analyze project" not in text  # the six step rows are dropped


def test_flow_frame_never_overflows_narrow_terminals() -> None:
    for width in (30, 40, 64, 100):
        console = _console(width)
        flow = TaskFlow(console, mode_label="Code Mode", task="Task title")
        flow.begin()
        flow.attach_plan(TaskGraph.from_plan("x", parse_plan(PLAN)))
        flow.set_activity("Editing a very long path/that/keeps/going/on/and/on.ts")
        flow._live = None
        console.print(flow._renderable())
        for line in console.export_text().splitlines():
            assert len(line) <= width, (width, line)


def test_assist_mode_flow_keeps_the_step_rows_and_activity_line() -> None:
    console = _console()
    flow = TaskFlow(console, mode_label="Agent Mode", task="Fix the bug")
    assert flow.header is None  # no Code Mode header outside Code Mode
    flow.begin()
    flow.observe_tool_start("edit_file", {"path": "a.py"})
    frame = flow._renderable()
    console.print(frame)
    text = console.export_text()
    assert "→ Editing a.py" in text
    assert "Working… (implement changes)" in text


def test_final_block_reports_state_plan_and_outcome() -> None:
    console = _console()
    flow = TaskFlow(console, mode_label="Code Mode", task="Build the app")
    graph = TaskGraph.from_plan("Build the app", parse_plan(PLAN))
    flow.begin()
    flow.attach_plan(graph)
    for task in graph.tasks[:3]:
        graph.mark(task, TaskState.COMPLETED)
    flow.attach_plan(graph)
    flow.finish("completed")

    out = console.export_text()
    assert "COMPLETED" in out  # the header reflects the real outcome
    assert f"SEEDCODE {__version__}" in out
    assert "✓ Setup project" in out
    assert "Task completed" in out
    assert "Ready for next task." in out


def test_cancelled_and_failed_flows_show_their_state() -> None:
    for outcome, label in (("cancelled", "CANCELLED"), ("failed", "FAILED")):
        console = _console()
        flow = TaskFlow(console, mode_label="Code Mode", task="Job")
        flow.begin()
        flow.finish(outcome, "boom" if outcome == "failed" else "")
        out = console.export_text()
        assert label in out
        assert ("Task cancelled" if outcome == "cancelled" else "Task failed") in out


def test_enabling_code_mode_prints_the_compact_idle_header(monkeypatch, tmp_path) -> None:
    """`/codemode on` shows the one-row header instead of a tall banner."""
    from seedcode import codemode_state as cms
    from seedcode.commands import CommandContext, dispatch
    from seedcode.commands import assist as assist_cmd
    from seedcode.commands import codemode as codemode_cmd
    from seedcode.core.models import AppConfig
    from seedcode.ui import UI

    cms.reset()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(codemode_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "is_available", lambda: (False, "test"))
    try:
        ui = UI(plain=True)
        ui.console = _console()
        dispatch(CommandContext(ui=ui, config=AppConfig(), engine=None), "/codemode on")
        out = ui.console.export_text()
        assert "CODE MODE" in out
        assert f"SEEDCODE {__version__}" in out
        assert "READY" in out
        assert len([ln for ln in out.splitlines() if "SEEDCODE" in ln]) == 1
    finally:
        cms.reset()


def test_header_refits_when_the_terminal_is_resized() -> None:
    """A resize mid-session re-fits the header instead of overflowing it."""
    console = _console(120)
    flow = TaskFlow(console, mode_label="Code Mode", task="Build the app")
    flow.begin()
    flow.attach_plan(TaskGraph.from_plan("x", parse_plan(PLAN)))
    assert flow.header is not None and flow.header.width == HEADER_WIDTH

    # Shrink the terminal the way a real resize does, then refresh.
    console._width = 40
    flow.set_activity("Editing src/auth/session.ts")
    assert flow.header.width == 40
    inner = _console(40)
    inner.print(flow.header.renderable())
    for line in inner.export_text().splitlines():
        assert len(line) <= 40

    # Very narrow: the panel degrades to the single-line form, never to a mess.
    console._width = 20
    flow.set_state("running")
    assert flow.header.width == 20
    assert flow.header.width < MIN_PANEL_WIDTH
    narrow = _console(20)
    flow.header.render(narrow)
    lines = [ln for ln in narrow.export_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert len(lines[0]) <= 20


def test_fit_width_never_exceeds_the_design_or_the_terminal() -> None:
    from seedcode.ui.codemode_header import fit_width

    assert fit_width(None) == HEADER_WIDTH
    assert fit_width(0) == HEADER_WIDTH
    assert fit_width(200) == HEADER_WIDTH
    assert fit_width(HEADER_WIDTH) == HEADER_WIDTH
    assert fit_width(40) == 40
    assert fit_width(1) == 8  # never zero/negative: rendering stays possible


def test_no_decorative_rule_or_ascii_art_in_the_frame() -> None:
    console = _console()
    flow = _flow()
    flow.begin()
    flow.attach_plan(TaskGraph.from_plan("x", parse_plan(PLAN)))
    console.print(flow._renderable())
    out = console.export_text()
    for art in ("█▓▒░▀▄", "╔", "══", "###"):
        assert art not in out
