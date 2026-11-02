"""v9.2.0 right-side TASK PLAN panel and bounded THINKING panel (Phases 5/6).

The plan panel renders the session's real task graph — one row per task with
the spec's state glyphs, a progress counter, and ``All tasks completed`` when
every entry is verified. The thinking panel is reserved headroom, bounded to
~30% of the terminal, and its manual scroll keeps the user's position.
"""

from __future__ import annotations

from textwrap import dedent

from seedcode.app import _TaskPresenter
from seedcode.core.tasks import TaskState
from seedcode.ui import taskpanel
from seedcode.ui.state import AppState
from seedcode.ui.tui import ChatTUI


def _plain(fragments) -> str:
    """Flatten prompt_toolkit-style (style, text) fragments to text lines."""
    lines: list[str] = []
    for fragment in fragments:
        text = "".join(t for _style, t in fragment)
        lines.append(text)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# taskpanel: pure rendering
# ---------------------------------------------------------------------------
def test_plan_panel_lines_match_the_spec_glyphs() -> None:
    rows = [
        ("completed", "Analyze project"),
        ("completed", "Inspect dependencies"),
        ("running", "Implement authentication"),
        ("pending", "Add validation"),
        ("pending", "Run tests"),
        ("failed", "Verify implementation"),
    ]
    lines = taskpanel.plan_panel_lines(rows, width=40)
    assert lines[0].strip().startswith("✓")
    assert lines[2].strip().startswith("●")
    assert lines[3].strip().startswith("○")
    assert lines[5].strip().startswith("✗")
    assert "2 / 6 completed" in lines[-1]


def test_all_tasks_completed_line_when_every_row_is_verified() -> None:
    rows = [("completed", "one"), ("completed", "two")]
    text = "\n".join(taskpanel.plan_panel_lines(rows, width=40))
    assert "All tasks completed" in text


def test_skipped_is_never_counted_as_completed_work() -> None:
    rows = [("completed", "one"), ("skipped", "optional")]
    lines = taskpanel.plan_panel_lines(rows, width=40)
    assert "1 / 2 completed" in "\n".join(lines)
    assert taskpanel.plan_progress(rows) == "1 / 2 completed"


def test_titles_are_clipped_to_the_panel_width() -> None:
    rows = [("pending", "very long task title " * 4)]
    lines = taskpanel.plan_panel_lines(rows, width=16)
    assert all(len(line) <= 16 for line in lines)


def test_empty_plan_renders_nothing() -> None:
    assert taskpanel.plan_panel_lines([], width=40) == []


def test_glyphs_survive_ascii_consoles() -> None:
    assert taskpanel.task_glyph("pending", legacy=True) == "o"
    assert taskpanel.task_glyph("completed", legacy=True) == "+"
    assert taskpanel.task_glyph("failed", legacy=True) == "x"
    assert taskpanel.task_glyph("running", legacy=True) == ">"


# ---------------------------------------------------------------------------
# AppState: real graph rows in, enum-normalized
# ---------------------------------------------------------------------------
def test_state_set_plan_normalizes_task_state_enums() -> None:
    state = AppState()
    state.set_plan(
        [
            (TaskState.COMPLETED, "Analyze"),
            (TaskState.RUNNING, "Implement"),
            (TaskState.PENDING, "Verify"),
        ]
    )
    assert state.plan_rows == [
        ("completed", "Analyze"),
        ("running", "Implement"),
        ("pending", "Verify"),
    ]


# ---------------------------------------------------------------------------
# ChatTUI: plan panel fragments follow real state
# ---------------------------------------------------------------------------
def _tui() -> ChatTUI:
    return ChatTUI(AppState(), width=100, height=40)


def test_plan_panel_hidden_without_an_active_plan() -> None:
    tui = _tui()
    assert tui._plan_height() == 0
    assert tui._plan_fragments(30, 1) == []
    dim = tui._plan_dimension()
    assert dim.min == 0 and dim.max == 0


def test_plan_panel_renders_rows_and_progress_in_real_state() -> None:
    tui = _tui()
    tui.state.set_plan(
        [
            ("completed", "Analyze project"),
            ("running", "Inspect dependencies"),
            ("pending", "Run tests"),
        ]
    )
    height = tui._plan_height()
    assert height == 4  # one row per task + the progress line
    text = _plain(tui._plan_fragments(30, height))
    assert "✓" in text and "Analyze project" in text
    assert "●" in text and "Inspect dependencies" in text
    assert "1 / 3 completed" in text
    dim = tui._plan_dimension()
    assert dim.min == height and dim.max == height


def test_plan_panel_clears_after_the_finish() -> None:
    tui = _tui()
    tui.state.set_plan([("completed", "only task")])
    assert "All tasks completed" in _plain(tui._plan_fragments(30, 2))
    tui.state.set_plan([])
    assert tui._plan_height() == 0


# ---------------------------------------------------------------------------
# ChatTUI: thinking panel bounds + scroll semantics
# ---------------------------------------------------------------------------
def test_thinking_panel_reserves_bounded_headroom_only_with_content() -> None:
    tui = _tui()  # height=40 → 30% = 12 rows
    assert tui._thinking_height() == 0  # nothing to show, nothing reserved
    for i in range(30):
        tui.state.append_thinking(f"reasoning line {i}\n")
    assert tui._thinking_height() == 12


def test_thinking_panel_auto_scrolls_to_the_newest_content() -> None:
    tui = _tui()
    for i in range(30):
        tui.state.append_thinking(f"reasoning line {i}\n")
    text = _plain(tui._thinking_fragments(40, tui._thinking_height()))
    assert "reasoning line 29" in text  # the newest line is visible
    assert "reasoning line 0" not in text


def test_manual_scroll_keeps_the_users_position() -> None:
    tui = _tui()
    for i in range(30):
        tui.state.append_thinking(f"reasoning line {i}\n")
    tui.state.scroll_thinking(6)  # scrolled up by 6 lines (kept on repaints)
    text = _plain(tui._thinking_fragments(40, tui._thinking_height()))
    # The newest line must NOT yank the view back down.
    assert "reasoning line 29" not in text
    assert "reasoning line 23" in text


def test_collapsed_panels_free_the_terminal() -> None:
    tui = _tui()
    tui.state.append_thinking("some reasoning\n")
    tui.state.collapse_thinking()  # turn end auto-collapses
    assert tui._thinking_height() == 0
    tui.state.toggle_thinking()  # Ctrl+B re-expands
    assert tui._thinking_height() > 0


# ---------------------------------------------------------------------------
# Presenter: plan rows publish from the real graph, and clear when empty
# ---------------------------------------------------------------------------
def test_presenter_publishes_graph_snapshots_into_the_plan_panel() -> None:
    class FakeGraph:
        total = 2

        def checklist(self, *, limit: int = 12):
            return [(TaskState.RUNNING, "implement panel"), (TaskState.PENDING, "verify panel")]

    state = AppState()
    tui = ChatTUI(state, width=80, height=24)

    class FakeUI:
        is_tui = True

    FakeUI.tui = tui
    presenter = _TaskPresenter(FakeUI())
    presenter._publish_plan_rows(FakeGraph(), 2)

    assert state.plan_rows == [("running", "implement panel"), ("pending", "verify panel")]

    presenter._publish_plan_rows(FakeGraph(), None)  # no active plan → cleared
    assert state.plan_rows == []
