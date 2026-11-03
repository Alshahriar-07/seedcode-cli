"""v9.3.2 main terminal UI: hierarchical TODO panel + compact thinking strip.

The right-side TODO list is the session's real task graph rendered as a tree
(real parent/child links, real states, real verified-work progress), and the
bottom strip is the minimal reasoning display directly above the composer.
The execution-status object the panel used to render remains backend state
only — no heading, model metadata or status fields reach the terminal.
"""

from __future__ import annotations

from seedcode.app import _TaskPresenter
from seedcode.core.tasks import (
    PlanItem,
    Task,
    TaskGraph,
    TaskState,
    parse_plan,
)
from seedcode.ui import taskpanel
from seedcode.ui.briefing import Briefing
from seedcode.ui.content import plain_text
from seedcode.ui.state import AppState
from seedcode.ui.tui import ChatTUI, calculated_plan_width


def _plain(fragments) -> str:
    return "\n".join("".join(t for _s, t in line) for line in fragments)


# ---------------------------------------------------------------------------
# hierarchy: the plan's own indentation becomes real parent/child links
# ---------------------------------------------------------------------------
NESTED_PLAN = """
1. Improve Main Terminal UI
   1. Inspect the TUI architecture
   2. Restore the TODO panel
2. Verify installation
"""


def test_indented_plan_lines_become_a_real_tree() -> None:
    items = parse_plan(NESTED_PLAN)
    graph = TaskGraph.from_plan("upgrade", items)
    assert [t.title for t in graph.tasks][0] == "Improve Main Terminal UI"
    parent = graph.tasks[0]
    assert parent.parent_id is None and parent.depth == 0
    children = graph.children_of(parent.id)
    assert [c.title for c in children] == [
        "Inspect the TUI architecture",
        "Restore the TODO panel",
    ]
    assert all(c.parent_id == parent.id and c.depth == 1 for c in children)
    assert graph.tasks[3].parent_id is None  # "Verify installation" is top level


def test_json_plans_can_declare_their_depth() -> None:
    items = parse_plan(
        '```plan\n{"tasks": [{"title": "parent"}, '
        '{"title": "child", "depth": 1}]}\n```'
    )
    graph = TaskGraph.from_plan("x", items)
    assert graph.tasks[1].parent_id == graph.tasks[0].id


def test_tree_rows_are_depth_first_with_container_flags() -> None:
    graph = TaskGraph.from_plan("upgrade", parse_plan(NESTED_PLAN))
    rows = graph.tree_rows()
    assert [(row[1], row[2], row[4]) for row in rows] == [
        ("Improve Main Terminal UI", 0, True),
        ("Inspect the TUI architecture", 1, False),
        ("Restore the TODO panel", 1, False),
        ("Verify installation", 0, False),
    ]


def test_collapsed_container_hides_only_its_subtree() -> None:
    graph = TaskGraph.from_plan("upgrade", parse_plan(NESTED_PLAN))
    rows = graph.tree_rows(collapsed=[graph.tasks[0].id])
    assert [row[1] for row in rows] == [
        "Improve Main Terminal UI",
        "Verify installation",
    ]
    # Collapsing is display-only: the graph's real state is untouched.
    assert len(graph.tasks) == 4


def test_container_is_never_scheduled_and_derives_its_state() -> None:
    graph = TaskGraph.from_plan("upgrade", parse_plan(NESTED_PLAN))
    parent = graph.tasks[0]
    assert graph.next_task() is graph.children_of(parent.id)[0]
    assert parent.state is TaskState.PENDING

    first, second = graph.children_of(parent.id)
    first.finish(TaskState.COMPLETED, "verified")
    graph.refresh_parents()
    assert parent.state is TaskState.RUNNING  # one child still open
    assert not graph.all_completed()

    second.finish(TaskState.COMPLETED, "verified")
    graph.refresh_parents()
    assert parent.state is TaskState.COMPLETED
    # Leaves: two subtasks completed, "Verify installation" still open.
    assert graph.percent() == 67


def test_a_parent_cannot_complete_over_a_failed_child() -> None:
    graph = TaskGraph.from_plan("upgrade", parse_plan(NESTED_PLAN))
    parent = graph.tasks[0]
    first, second = graph.children_of(parent.id)
    first.finish(TaskState.COMPLETED, "verified")
    second.finish(TaskState.FAILED, "unverified")
    graph.refresh_parents()
    assert parent.state is TaskState.FAILED
    assert not graph.all_completed()


def test_progress_percent_counts_verified_leaves_only() -> None:
    graph = TaskGraph.from_plan("upgrade", parse_plan(NESTED_PLAN))
    assert graph.percent() == 0
    graph.tasks[1].finish(TaskState.SKIPPED, "not needed")
    assert graph.percent() == 0  # a skip is never verified work
    graph.tasks[2].finish(TaskState.COMPLETED, "verified")
    # Leaves: the two subtasks plus "Verify installation" → 1 of 3 verified.
    assert graph.percent() == 33


def test_parent_links_survive_a_checkpoint_round_trip() -> None:
    graph = TaskGraph.from_plan("upgrade", parse_plan(NESTED_PLAN))
    graph.tasks[0].finish(TaskState.RUNNING, "running")
    restored = TaskGraph.from_dict(graph.to_dict())
    assert [t.parent_id for t in restored.tasks] == [
        t.parent_id for t in graph.tasks
    ]
    assert [t.depth for t in restored.tasks] == [t.depth for t in graph.tasks]
    assert restored.tasks[0].state is TaskState.RUNNING


# ---------------------------------------------------------------------------
# taskpanel: the tree renderer
# ---------------------------------------------------------------------------
def test_tree_panel_renders_nesting_arrows_and_blocked_state() -> None:
    rows = [
        ("running", "Improve Main Terminal UI", 0, 1, True),
        ("completed", "Inspect the TUI architecture", 1, 2, False),
        ("blocked", "Restore the TODO panel", 1, 3, False),
    ]
    lines = taskpanel.tree_panel_lines(rows, width=40, percent=50)
    assert lines[0].startswith("▼ ● Improve Main Terminal UI")
    assert lines[1].startswith("  ✓ Inspect the TUI architecture")
    assert lines[2].startswith("  ◷ Restore the TODO panel")
    # Only the leaves are counted (the parent is the summary row).
    assert lines[3] == "Progress: 50% (1 / 2 completed)"


def test_tree_panel_marks_collapsed_containers() -> None:
    rows = [("pending", "Parent", 0, 7, True)]
    assert taskpanel.tree_panel_lines(rows, 30, collapsed={7})[0].startswith("▶")
    assert taskpanel.tree_panel_lines(rows, 30)[0].startswith("▼")


def test_tree_panel_clips_and_hides_empty_plans() -> None:
    rows = [("pending", "a very long task title " * 4, 0, 1, False)]
    assert all(len(line) <= 18 for line in taskpanel.tree_panel_lines(rows, 18))
    assert taskpanel.tree_panel_lines([], 40) == []


# ---------------------------------------------------------------------------
# state: tree snapshot + collapse are real state
# ---------------------------------------------------------------------------
def test_set_plan_tree_keeps_flat_rows_for_the_compact_view() -> None:
    state = AppState()
    state.set_plan_tree(
        [
            (TaskState.RUNNING, "Parent", 0, 1, True),
            (TaskState.COMPLETED, "Child", 1, 2, False),
        ],
        percent=50,
    )
    assert state.plan_rows == [("running", "Parent"), ("completed", "Child")]
    assert state.plan_nodes[1] == ("completed", "Child", 1, 2, False)
    assert state.plan_percent == 50


def test_toggle_plan_collapse_round_trips_the_real_ids() -> None:
    state = AppState()
    state.set_plan_tree(
        [(TaskState.PENDING, "Parent", 0, 4, True), (TaskState.PENDING, "Child", 1, 5, False)]
    )
    state.toggle_plan_collapse(4)
    assert state.plan_collapsed == [4]
    state.toggle_plan_collapse()
    assert state.plan_collapsed == []  # everything open again


# ---------------------------------------------------------------------------
# briefing: backend state only (v9.3.2 — no dashboard render in the terminal)
# ---------------------------------------------------------------------------
def test_the_briefing_state_reports_only_real_events() -> None:
    """The backend status survives even though its dashboard does not."""
    b = Briefing(
        active=True,
        status="Executing",
        task="Backend development",
        activity="Updating API endpoints",
        last_completed="Project analysis",
        next_action="Run API tests",
    )
    assert b.active is True
    assert b.status == "Executing"
    assert b.task == "Backend development"
    assert b.activity == "Updating API endpoints"
    b.clear()
    assert b.active is False and b.status == "" and b.retry == "" and b.error == ""


def test_retry_and_error_are_reported_only_when_real() -> None:
    b = Briefing(active=True, status="Retrying", retry="attempt 2/4, next in 4s")
    assert b.retry == "attempt 2/4, next in 4s"
    assert b.error == ""  # no error is not fabricated
    b.error = "Connection interrupted"
    assert b.error == "Connection interrupted"


# ---------------------------------------------------------------------------
# ChatTUI: layout, tree panel, briefing strip, activity log
# ---------------------------------------------------------------------------
def _tui(width: int = 100, height: int = 40) -> ChatTUI:
    return ChatTUI(AppState(), width=width, height=height)


def _nested_state(tui: ChatTUI) -> TaskGraph:
    graph = TaskGraph.from_plan("upgrade", parse_plan(NESTED_PLAN))
    tui.state.set_plan_tree(
        graph.tree_rows(), percent=graph.percent()
    )
    return graph


def test_todo_panel_renders_the_hierarchy_on_the_right() -> None:
    tui = _tui()
    _nested_state(tui)
    text = _plain(tui._plan_fragments(34, tui._plan_height()))
    assert "▼" in text and "Improve Main Terminal UI" in text
    assert "  ○ Inspect the TUI architecture" in text
    assert "Progress: 0% (0 / 3 completed)" in text
    # The panel keeps a real width beside the chat, never replaces it.
    assert 0 < calculated_plan_width(tui.state.plan_nodes, 100) <= 34


def test_collapsed_todo_tree_still_shows_the_parent_and_progress() -> None:
    tui = _tui()
    graph = _nested_state(tui)
    tui.state.toggle_plan_collapse(graph.tasks[0].id)
    text = _plain(tui._plan_fragments(34, tui._plan_height()))
    assert "▶" in text
    assert "Inspect the TUI architecture" not in text
    assert "Progress:" in text


def test_narrow_terminals_keep_a_compact_todo_column() -> None:
    rows = [("pending", "Short task", 0, 1, False)]
    assert calculated_plan_width(rows, 60) > 0  # compact, not removed
    assert calculated_plan_width(rows, 40) == 0  # too narrow to split at all
    assert calculated_plan_width([], 120) == 0


def test_the_thinking_strip_sits_directly_above_the_composer() -> None:
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    tui = _tui()
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        app = tui._build_app()
    root = app.layout.container
    children = list(root.children)
    thinking_index = children.index(tui._thinking_window)
    # The composer (Frame) is the very next region; the hint row follows it.
    assert thinking_index == len(children) - 3
    assert children[thinking_index + 2] is tui._hint_window


def test_the_thinking_strip_is_compact_and_shows_no_model_metadata() -> None:
    tui = _tui()
    tui.state.update(provider="OpenRouter", model="DeepSeek V4.1 Flash")
    tui.state.set_briefing(active=True, status="Executing", activity="Editing app.py")
    tui.state.append_thinking("Checking the composer layout first...\n")
    tui._reallocate_rows()
    rows = tui._thinking_height()
    assert 0 < rows <= 6  # compact: never a large dedicated dashboard
    assert tui.min_layout_rows() + 1 <= tui._rows
    text = _plain(tui._thinking_fragments(tui._width, rows))
    assert "Thinking..." in text
    assert "Checking the composer layout first..." in text
    assert "DeepSeek" not in text and "OpenRouter" not in text
    assert "Executing" not in text  # execution status stays in backend state


def test_the_thinking_strip_hides_when_empty_and_short_terminals_stay_usable() -> None:
    tui = _tui()
    tui.state.reset_thinking()  # no reasoning, no section
    assert tui._thinking_height() == 0

    short = _tui(height=9)
    short.state.append_thinking("some reasoning\n")
    short._reallocate_rows()
    assert short._thinking_height() == 0  # conversation/composer win on tiny screens
    assert short.min_layout_rows() <= max(short._rows, 1)


def test_activity_lines_carry_no_bracketed_timestamps() -> None:
    """v9.3.2: the activity log never prints a bracketed wall-clock stamp.

    The "[TIME 12:34:56]" beside AI responses was this prefix (root cause);
    the real timestamp survives only in the backend ActivityEvent.
    """
    tui = _tui()
    tui.append_activity("action", "Reading package.json")
    text = plain_text(tui.buffer.lines())
    assert "Reading package.json" in text
    assert "[TIME" not in text
    import re as _re

    assert not _re.search(r"\[\d{2}:\d{2}:\d{2}\]", text)  # no [HH:MM:SS]


# ---------------------------------------------------------------------------
# presenter: the panel and the briefing follow the same real events
# ---------------------------------------------------------------------------
class _FakeUI:
    is_tui = True

    def __init__(self, tui: ChatTUI) -> None:
        self.tui = tui
        self.state = tui.state

    def dim(self, _message: str) -> None: ...
    def warning(self, _message: str) -> None: ...
    def success(self, _message: str) -> None: ...
    def error(self, _message: str) -> None: ...
    def blank(self) -> None: ...
    def append_activity(self, kind: str, message: str) -> None:
        self.tui.append_activity(kind, message)


def test_presenter_publishes_the_real_tree_and_percent() -> None:
    tui = _tui()
    presenter = _TaskPresenter(_FakeUI(tui))
    graph = TaskGraph.from_plan("upgrade", parse_plan(NESTED_PLAN))
    graph.tasks[1].finish(TaskState.COMPLETED, "verified")
    presenter._publish_plan_rows(graph, graph.total)

    assert [node[1] for node in tui.state.plan_nodes][:2] == [
        "Improve Main Terminal UI",
        "Inspect the TUI architecture",
    ]
    assert tui.state.plan_percent == 33  # 1 of 3 leaf tasks verified
    presenter._publish_plan_rows(None, None)
    assert tui.state.plan_nodes == []


def test_presenter_updates_the_briefing_from_tool_events() -> None:
    tui = _tui()
    presenter = _TaskPresenter(_FakeUI(tui))
    tui.state.update(provider="Ollama", model="qwen3-coder")

    presenter.on_step(
        {
            "phase": "tool_start",
            "name": "read_file",
            "args": {"path": "seedcode/ui/tui.py"},
            "message": "read_file started",
        }
    )
    assert tui.state.briefing.active
    assert tui.state.briefing.status == "Executing"
    assert "seedcode/ui/tui.py" in tui.state.briefing.activity

    presenter.on_step(
        {
            "phase": "tool_done",
            "name": "read_file",
            "args": {"path": "seedcode/ui/tui.py"},
            "ok": True,
            "message": "Read seedcode/ui/tui.py",
        }
    )
    assert tui.state.briefing.last_completed == "Read seedcode/ui/tui.py"
    assert tui.state.briefing.status == "Working"


def test_presenter_reports_a_real_retry_in_the_briefing() -> None:
    tui = _tui()
    presenter = _TaskPresenter(_FakeUI(tui))
    presenter.on_event("retry", "provider error, retrying in 4s")
    assert tui.state.briefing.status == "Retrying"
    assert "retrying in 4s" in tui.state.briefing.retry


def test_session_events_drive_task_and_testing_states() -> None:
    from seedcode.core import session as session_mod

    tui = _tui()
    presenter = _TaskPresenter(_FakeUI(tui))
    graph = TaskGraph.from_plan("upgrade", parse_plan(NESTED_PLAN))
    fake = type("FakeSession", (), {"graph": graph, "model_calls": 1})()
    session_mod.set_current_session(fake)
    try:
        presenter.on_session_event("task_start", "Task 1/4: Improve Main Terminal UI")
        assert tui.state.briefing.task.startswith("Task 1/4")
        presenter.on_session_event("final_verify", "")
        assert tui.state.briefing.status == "Testing"
        presenter.on_session_event("task_blocked", "Task 2 is blocked: no decision")
        assert tui.state.briefing.status == "Blocked"
        assert "no decision" in tui.state.briefing.error
    finally:
        session_mod.reset()
