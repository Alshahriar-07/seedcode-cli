"""Live, step-by-step task progress for Code Mode / Agent Mode (v8.1.0).

This is a *truthful* progress view, not a decoration. Every step state is
driven by an event the engine actually produced:

* ``analyze``   completes when the model's first output for this turn arrives
                (a plan message or the first tool call);
* ``inspect``   completes when a read-only tool (read_file, search_text, ...)
                has actually returned successfully;
* ``plan``      completes when the model narrated its approach (a text step);
* ``implement`` completes when a mutating tool (write_file, edit_file, ...)
                has actually succeeded;
* ``test``      completes only when a recognised test command really ran and
                exited 0 — a run that failed is shown as FAILED, and a turn
                that never ran tests shows the step as SKIPPED;
* ``verify``    completes when the final answer was produced.

A step that is still pending when the turn ends is reported as ``skipped``
("not needed"), never as completed, so the UI can never claim work that did
not happen. Failed tasks and their reason stay on screen, and the flow always
ends by returning control to the CLI prompt — nothing here can end the
process.

v7.1.0 layout (compact, professional):

* the compact Code Mode header (:mod:`.codemode_header`) replaces the tall
  banner — state, ``Task n/N``, the active task, progress bar, elapsed time and
  call count, in two rows;
* the plan is rendered as a short checklist (``✓ ● ○ ✗``) when the persistent
  Code Mode session attaches its task graph;
* one live activity line (``→ Editing src/auth/session.ts``) replaces the old
  stream of raw tool chatter, and no decorative rule or blank row is drawn.
"""

from __future__ import annotations

import enum
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Sequence

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.text import Text

from ..utils.text import safe_text
from .codemode_header import (
    HEADER_WIDTH,
    CodeModeHeader,
    fit_width,
    live_action_line,
    task_checklist,
)
from .layout import supports_unicode

__all__ = ["TaskFlow", "TaskState", "TaskStep", "activity_for", "step_glyph"]


# --- states -----------------------------------------------------------------
class TaskState(str, enum.Enum):
    """The lifecycle of one task step (see the module docstring)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# Unicode / ASCII glyphs per state, so a legacy Windows console stays readable.
_GLYPHS: dict[TaskState, str] = {
    TaskState.PENDING: "○",
    TaskState.RUNNING: "●",
    TaskState.COMPLETED: "✓",
    TaskState.FAILED: "✗",
    TaskState.SKIPPED: "–",
}
_GLYPHS_ASCII: dict[TaskState, str] = {
    TaskState.PENDING: "-",
    TaskState.RUNNING: ">",
    TaskState.COMPLETED: "[ok]",
    TaskState.FAILED: "[x]",
    TaskState.SKIPPED: "-",
}

_STYLES: dict[TaskState, str] = {
    TaskState.PENDING: "seed.dim",
    TaskState.RUNNING: "seed.accent",
    TaskState.COMPLETED: "seed.success",
    TaskState.FAILED: "seed.error",
    TaskState.SKIPPED: "seed.dim",
}


def step_glyph(state: TaskState, *, legacy: bool = False) -> str:
    """The marker for ``state`` (ASCII on legacy consoles)."""
    return (_GLYPHS_ASCII if legacy else _GLYPHS)[state]


# --- tool classification ----------------------------------------------------
# Read-only inspection tools. Everything else that mutates is a change.
_INSPECT_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "find_files",
        "search_text",
        "project_index",
        "computer_see",
        "computer_state",
        "desktop_screen_info",
        "desktop_screenshot",
        "desktop_windows",
        "ui_assert",
        "ui_wait_for",
    }
)

# git sub-commands that only read.
_GIT_READ = frozenset({"status", "diff", "log", "show", "branch", "remote", "rev-parse"})

# A command counts as a test run only when it really is one.
_TEST_COMMAND_RE = re.compile(
    r"(?:^|[\s;&|(])"
    r"(?:pytest|py\.test|unittest|tox|nox|ctest|jest|vitest|mocha|rspec|phpunit|"
    r"cargo\s+test|go\s+test|dotnet\s+test|gradle\s+test|mvn\s+test|"
    r"npm\s+(?:run\s+)?test|yarn\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test|"
    r"bun\s+test|deno\s+test|make\s+test|rake\s+test|python3?\s+-m\s+pytest|"
    r"python3?\s+-m\s+unittest)",
    re.IGNORECASE,
)

# "721 passed" / "12 passing" / cargo's "test result: ok. 5 passed" — real
# evidence parsed from the run, with the wording each runner actually uses.
_TEST_RESULT_RES = (
    (re.compile(r"test result:\s*ok\.\s*(\d+)\s+passed", re.IGNORECASE), "{0} passed"),
    (re.compile(r"(\d+)\s+passed"), "{0} passed"),
    (re.compile(r"(\d+)\s+passing"), "{0} passing"),
)

_TEXT_CLIP = 68


def _clip(value: str, limit: int = _TEXT_CLIP) -> str:
    # v7.1.0: tool/command text reaching the view is normalized, so a lone
    # surrogate in output can never raise while the task view is drawn.
    value = " ".join(safe_text(value).split())
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)] + "…"


def _is_inspect(name: str, args: dict[str, Any]) -> bool:
    if name == "git":
        sub = str(args.get("command") or args.get("args") or "").split()
        return not sub or sub[0].lower() in _GIT_READ
    return name in _INSPECT_TOOLS


def _is_test_command(name: str, args: dict[str, Any]) -> bool:
    if name != "run_command":
        return False
    command = str(args.get("command") or "")
    return bool(_TEST_COMMAND_RE.search(command))


def _test_summary(output: str, ok: bool) -> str:
    """Compact evidence for a finished test run (never invents a result)."""
    if not ok:
        # A failed run reports what failed, never a passing count.
        failed = re.search(r"(\d+)\s+failed", output or "")
        return f"{failed.group(1)} failed" if failed else "test run failed"
    for pattern, label in _TEST_RESULT_RES:
        match = pattern.search(output or "")
        if match:
            return label.format(match.group(1))
    return "exit 0"


def _test_detail(args: dict[str, Any], summary: str) -> str:
    """Step detail for a test step: the result, then the command that produced it."""
    command = str(args.get("command") or "")
    return f"{summary} — {_clip(command, 40)}" if command else summary


# --- steps ------------------------------------------------------------------
@dataclass
class TaskStep:
    """One visible step of a task."""

    key: str
    label: str
    state: TaskState = TaskState.PENDING
    detail: str = ""

    def set(self, state: TaskState, detail: str = "") -> None:
        self.state = state
        self.detail = detail


# The canonical pipeline, in order. Steps that this particular task does not
# need are shown as skipped instead of being faked as complete.
DEFAULT_STEPS: tuple[tuple[str, str], ...] = (
    ("analyze", "Analyze project"),
    ("inspect", "Inspect files"),
    ("plan", "Plan implementation"),
    ("implement", "Implement changes"),
    ("test", "Run tests"),
    ("verify", "Verify result"),
)


class TaskFlow:
    """A compact live task view: title, real step states, and an outcome.

    Rendering is fully guarded: a console problem degrades to plain text and
    can never interrupt the task it is reporting on.
    """

    def __init__(
        self,
        console: Console | None,
        *,
        mode_label: str = "Task",
        task: str = "",
        steps: Sequence[tuple[str, str]] | None = None,
        legacy: bool = False,
        ui=None,
        code_mode: bool | None = None,
    ) -> None:
        self._console = console
        # The owning UI, when there is one: used only so a permission dialog
        # can pause this display while it asks (see UI.register_live).
        self._ui = ui
        self._legacy = legacy
        self.mode_label = mode_label
        self.task = _clip(task, 80)
        self.steps: list[TaskStep] = [
            TaskStep(key, label) for key, label in (steps or DEFAULT_STEPS)
        ]
        self._live: Live | None = None
        self._analysis_done = False
        self._saw_plan = False
        # Args of in-flight calls, per tool name and in issue order, so a
        # tool_done report lands on the step its tool_start opened.
        self._args_in_flight: dict[str, list[dict[str, Any]]] = {}
        self._changed: set[str] = set()
        self._changes = 0
        self._finished = False
        # --- v7.1.0 compact Code Mode view --------------------------------
        self._code_mode = (
            "code mode" in mode_label.lower() if code_mode is None else code_mode
        )
        width = console.size.width if isinstance(console, Console) else HEADER_WIDTH
        self.header = (
            CodeModeHeader(width=fit_width(width), legacy=legacy)
            if self._code_mode
            else None
        )
        self._plan_rows: list[tuple[str, str]] = []
        self._plan_total = 0
        self._plan_index = 0
        self._activity = ""
        self._calls = 0
        self._state = "running"
        self._started_at = 0.0

    # --- construction ------------------------------------------------------
    @classmethod
    def for_ui(cls, ui, *, mode_label: str, task: str) -> "TaskFlow | None":
        """Build a flow for ``ui``, or None when it has no Rich console.

        Test doubles and embedders that only implement the messaging methods
        simply get no task view — the turn is otherwise unaffected.
        """
        console = getattr(ui, "console", None)
        if not isinstance(console, Console):
            return None
        return cls(
            console,
            mode_label=mode_label,
            task=task,
            legacy=not supports_unicode(console),
            ui=ui,
        )

    # --- v7.1.0: the plan, the live action line, and the header ------------
    def attach_plan(self, graph) -> None:
        """Bind a Code Mode task graph so the view shows the real plan."""
        if graph is None:
            return
        self._plan_rows = list(graph.checklist())
        self._plan_total = int(graph.total)
        done, total = graph.progress()
        self._plan_index = min(done + (0 if graph.all_completed() else 1), total)
        self._sync_header()
        self._refresh()

    def set_state(self, state: str) -> None:
        """Set the header's state label (running/paused/blocked/completed…)."""
        self._state = state
        self._sync_header()
        self._refresh()

    def set_activity(self, activity: str) -> None:
        """Update the single live activity line."""
        self._activity = " ".join(safe_text(activity).split())
        self._sync_header()
        self._refresh()

    def set_progress(self, index: int, total: int, title: str = "") -> None:
        """Point the header at the active task (1-based index)."""
        self._plan_index = max(1, int(index))
        self._plan_total = max(0, int(total))
        if title:
            self.task = _clip(title, 80)
        self._sync_header()
        self._refresh()

    def note_call(self, count: int | None = None) -> None:
        """Count a model/tool call (session-reported, never invented)."""
        self._calls = (self._calls + 1) if count is None else max(0, int(count))
        self._sync_header()
        self._refresh()

    def _progress_percent(self) -> int:
        if self._plan_total <= 0:
            done = sum(1 for s in self.steps if s.state is TaskState.COMPLETED)
            total = len(self.steps) or 1
            return int(round(100 * done / total))
        done = sum(
            1 for state, _ in self._plan_rows if str(getattr(state, "value", state)) == "completed"
        )
        return int(round(100 * done / self._plan_total))

    def _sync_header(self) -> None:
        header = self.header
        if header is None:
            return
        # Re-fit on every refresh: a terminal resized mid-session must not leave
        # a panel (or a progress bar) wider than the screen. Below the panel
        # threshold the header degrades to its single-line form by itself.
        if isinstance(self._console, Console):
            header.width = fit_width(
                getattr(self._console.size, "width", HEADER_WIDTH)
            )
        header.update(
            state=self._state,
            task_index=self._plan_index,
            task_total=self._plan_total,
            task_title=self.task,
            percent=self._progress_percent(),
            elapsed_s=self._elapsed(),
            calls=self._calls,
            activity=self._activity,
        )

    def _elapsed(self) -> float:
        return max(0.0, time.monotonic() - self._started_at) if self._started_at else 0.0

    def _header_lines(self) -> list[RenderableType]:
        header = self.header
        if header is None:
            return []
        self._sync_header()
        return [header.renderable()]

    def _plan_lines(self) -> list[Text]:
        if not self._plan_rows:
            return []
        return task_checklist(self._plan_rows, legacy=self._legacy)

    # --- lookup ------------------------------------------------------------
    def failed_steps(self) -> list[TaskStep]:
        """Steps that ended in a real failure (kept visible in the summary)."""
        return [item for item in self.steps if item.state is TaskState.FAILED]

    def step(self, key: str) -> TaskStep | None:
        for item in self.steps:
            if item.key == key:
                return item
        return None

    def _skip_pending_before(self, key: str) -> None:
        """Close every still-pending step that comes before ``key``.

        Used when the task skips ahead (a direct edit with no inspection, for
        example): the missed steps are honestly marked skipped, not done.
        """
        for item in self.steps:
            if item.key == key:
                return
            if item.state is TaskState.PENDING:
                item.set(TaskState.SKIPPED, "not needed")

    # --- transitions -------------------------------------------------------
    def begin(self) -> "TaskFlow":
        """Mark the task started: 'analyze' is genuinely running now."""
        self._started_at = time.monotonic()
        self._state = "running"
        self.update("analyze", TaskState.RUNNING, "reading the request")
        self._sync_header()
        return self

    def update(self, key: str, state: TaskState, detail: str = "") -> None:
        """Set one step's state and refresh the live view."""
        target = self.step(key)
        if target is None:
            return
        if state in (TaskState.RUNNING, TaskState.COMPLETED):
            self._skip_pending_before(key)
        target.set(state, detail)
        self._refresh()

    def observe_text(self, text: str) -> None:
        """The model narrated its approach — the plan step is real now."""
        if self._saw_plan or not _clip(text).strip():
            return
        self._saw_plan = True
        plan = self.step("plan")
        if plan is not None and plan.state in (TaskState.PENDING, TaskState.RUNNING):
            self.update("plan", TaskState.COMPLETED, _clip(text, 56))

    def observe_tool_start(self, name: str, args: dict[str, Any] | None = None) -> None:
        """A tool really started; move the matching step to running."""
        args = args or {}
        self._args_in_flight.setdefault(name, []).append(args)
        self._calls += 1
        self.set_activity(activity_for(name, args))
        self._complete_analysis()
        if _is_inspect(name, args):
            self.update("inspect", TaskState.RUNNING, _describe(name, args))
        elif _is_test_command(name, args):
            # Testing does not imply that anything was edited: steps before this
            # one that never ran stay skipped (handled by update()).
            self.update("test", TaskState.RUNNING, _clip(str(args.get("command") or ""), 56))
        else:
            self.update("implement", TaskState.RUNNING, _describe(name, args))

    def observe_tool_done(
        self, name: str, ok: bool, output: str = "", args: dict[str, Any] | None = None
    ) -> None:
        """A tool really finished; the owning step completes or fails."""
        pending = self._args_in_flight.get(name) or []
        remembered = pending.pop(0) if pending else None
        args = args or remembered or {}
        if _is_inspect(name, args):
            self.update(
                "inspect",
                TaskState.COMPLETED if ok else TaskState.FAILED,
                _clip(output, 56),
            )
            return
        if _is_test_command(name, args):
            self.update(
                "test",
                TaskState.COMPLETED if ok else TaskState.FAILED,
                _test_detail(args, _test_summary(output, ok)),
            )
            return
        if ok:
            self._changes += 1
            path = str(args.get("path") or args.get("source") or "").strip()
            if path:
                self._changed.add(path)
            self.update("implement", TaskState.COMPLETED, self._change_detail())
        else:
            self.update("implement", TaskState.FAILED, _clip(output, 56))

    def _complete_analysis(self) -> None:
        if not self._analysis_done:
            self._analysis_done = True
            self.update("analyze", TaskState.COMPLETED, "request understood")

    def _change_detail(self) -> str:
        if self._changed:
            return f"{len(self._changed)} file(s) changed"
        if self._changes:
            return f"{self._changes} change(s) applied"
        return "change applied"

    # --- rendering ---------------------------------------------------------
    def _lines(self) -> list[Text]:
        head = Text(no_wrap=True, overflow="crop")
        head.append("Task", style="bold seed.primary")
        head.append(f"  ·  {self.mode_label}", style="seed.dim")
        lines: list[Text] = [head]
        if self.task and not self._plan_rows:
            lines.append(Text(_clip(self.task, 80), style="seed.text", no_wrap=True))
        for item in self.steps:
            line = Text(no_wrap=True, overflow="crop")
            line.append(
                f"{step_glyph(item.state, legacy=self._legacy)} ",
                style=_STYLES[item.state],
            )
            line.append(
                item.label,
                style="seed.text" if item.state is not TaskState.PENDING else "seed.dim",
            )
            if item.detail:
                line.append(f"  {item.detail}", style="seed.dim")
            lines.append(line)
        return lines

    def _body_lines(self) -> list[Text]:
        """The fine-grained step rows.

        When the persistent session attached a plan, the checklist is the task
        view — six extra step rows would only add height without adding
        information, so they are dropped (v7.1.0 compact layout).
        """
        return [] if self._plan_rows else self._lines()

    def _renderable(self) -> RenderableType:
        parts: list[RenderableType] = []
        parts.extend(self._header_lines())
        parts.extend(self._plan_lines())
        parts.extend(self._body_lines())
        # A finished flow never claims to be working: the live block drops the
        # activity footer once the outcome has been decided.
        if not self._finished:
            if self.header is None:
                # Without the header (Agent Mode) the activity line is the
                # only place the live action shows.
                activity = live_action_line(self._activity, legacy=self._legacy)
                if activity is not None:
                    parts.append(activity)
            running = next(
                (item for item in self.steps if item.state is TaskState.RUNNING), None
            )
            label = f"Working… ({running.label.lower()})" if running else "Working…"
            parts.append(Text(f"  {label}", style="seed.accent"))
        return Group(*parts)

    def _refresh(self) -> None:
        if self._live is not None:
            try:
                self._live.update(self._renderable())
            except Exception:
                pass  # a rendering hiccup must never break a task

    def start(self) -> None:
        """Show the live block (transient: the final block is printed once)."""
        if self._console is None:
            return
        try:
            self._live = Live(
                self._renderable(),
                console=self._console,
                refresh_per_second=12,
                transient=True,
            )
            self._live.start()
            register = getattr(self._ui, "register_live", None)
            if callable(register):
                register(self._live)
        except Exception:
            self._live = None

    def stop(self) -> None:
        live, self._live = self._live, None
        if live is None:
            return
        unregister = getattr(self._ui, "unregister_live", None)
        if callable(unregister):
            unregister(live)
        try:
            live.stop()
        except Exception:
            pass

    def finish(self, outcome: str = "completed", reason: str = "") -> None:
        """Stop the live view and print the persistent final block.

        ``outcome`` is one of ``completed`` / ``failed`` / ``cancelled``. Any
        step still pending is reported as skipped — the flow never claims work
        that did not happen. The CLI keeps running either way.
        """
        if self._finished:
            return
        self._finished = True
        for item in self.steps:
            if item.state in (TaskState.PENDING, TaskState.RUNNING):
                item.set(TaskState.SKIPPED, "not needed")
        self._state = {
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(outcome, outcome or "completed")
        self._sync_header()
        self.stop()
        if self._console is None:
            return
        # Printed line by line, each guarded: an unencodable glyph on an exotic
        # console can skip that one line but never truncates the report.
        for renderable in self._header_lines():
            self._print(renderable)
        for line in self._plan_lines():
            self._print(line)
        for line in self._body_lines():
            self._print(line)
        self._print(self._outcome_line(outcome, reason))
        for extra in self._summary_lines(outcome):
            self._print(Text(f"  {extra}", style="seed.dim"))
        self._print(Text(self._ready_line(outcome), style="seed.accent"))
        self._print(Text(""))

    def _print(self, renderable: RenderableType) -> None:
        try:
            self._console.print(renderable)
        except Exception:
            pass  # never let a display problem break a task report

    def _outcome_line(self, outcome: str, reason: str) -> Text:
        cancel_mark = ":" if self._legacy else "■"
        if outcome == "failed":
            line = Text()
            line.append(f"{step_glyph(TaskState.FAILED, legacy=self._legacy)} ", style="seed.error")
            line.append("Task failed", style="seed.error")
            if reason:
                line.append(f"  —  {_clip(reason, 90)}", style="seed.dim")
            return line
        if outcome == "cancelled":
            line = Text()
            line.append(f"{cancel_mark} ", style="seed.warning")
            line.append("Task cancelled", style="seed.warning")
            return line
        line = Text()
        line.append(f"{step_glyph(TaskState.COMPLETED, legacy=self._legacy)} ", style="seed.success")
        line.append("Task completed", style="seed.success")
        return line

    def _summary_lines(self, outcome: str) -> list[str]:
        """Only facts observed during this task — never an invented result."""
        lines: list[str] = []
        failed = self.failed_steps()
        if failed:
            names = ", ".join(item.label for item in failed)
            lines.append(f"{len(failed)} step(s) failed: {names}")
        if outcome != "completed":
            return lines
        test = self.step("test")
        if self._changed:
            names = sorted(self._changed)[:3]
            more = "" if len(self._changed) <= 3 else f" (+{len(self._changed) - 3} more)"
            lines.append(f"{len(self._changed)} file(s) changed: {', '.join(names)}{more}")
        elif self._changes:
            lines.append(f"{self._changes} change(s) applied")
        if test is not None and test.state is TaskState.COMPLETED and test.detail:
            lines.append(f"Tests: {test.detail}")
        return lines

    def _ready_line(self, outcome: str) -> str:
        return "Ready for another task." if outcome == "failed" else "Ready for next task."


def activity_for(name: str, args: dict[str, Any] | None = None) -> str:
    """The single live action line for a tool call (v7.1.0 UX).

    Verbs match what a software engineer would say out loud: editing a file,
    running a command, running the tests, searching, fixing a failure.
    """
    args = args or {}
    path = str(args.get("path") or args.get("source") or args.get("destination") or "")
    command = str(args.get("command") or "")
    if name in ("write_file", "edit_file", "patch_file", "apply_patch"):
        return f"Editing {path or 'the project'}"
    if name in ("read_file",):
        return f"Reading {path or 'the project'}"
    if name in ("search_text", "find_files", "project_index", "list_dir"):
        target = str(args.get("pattern") or args.get("query") or args.get("path") or "")
        return f"Searching {target or 'the project'}"
    if name == "run_command":
        return f"Running {_clip(command, 52)}" if command else "Running a command"
    if name == "git":
        return f"Running git {_clip(command, 44)}"
    if name.startswith("computer_") or name.startswith("ui_") or name.startswith("desktop_"):
        target = str(args.get("target") or args.get("skill") or "the desktop")
        return f"Controlling {target}"
    return _describe(name, args)


def _describe(name: str, args: dict[str, Any]) -> str:
    """A compact, human description of a tool call (no raw JSON dump)."""
    if name == "run_command":
        return _clip(str(args.get("command") or ""), 56)
    for key in ("path", "source", "pattern", "query", "command"):
        value = args.get(key)
        if value:
            return _clip(f"{name} {value}", 56)
    if args:
        try:
            return _clip(f"{name} {json.dumps(args, ensure_ascii=False)}", 56)
        except (TypeError, ValueError):
            pass
    return name
