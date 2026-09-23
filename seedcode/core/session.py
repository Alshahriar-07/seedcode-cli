"""Persistent Code Mode session (v7.1.0): the loop that finishes the project.

The loop in :mod:`seedcode.core.agent` runs one *turn*: detect tool calls, run
them, ask the model again, answer. That is a chat primitive; a model response
ending is not a task ending. This module is the layer above it that makes Code
Mode a professional long-running coding agent::

    plan the request
      -> for each task (dependency order):
           work -> model/tool cycles -> verify acceptance criteria
           -> recover and retry on failure -> verify again
           -> task COMPLETED (only on verified evidence)
      -> next task
      -> final verification (tests / build / acceptance)
      -> session completed, or a recovery task is created

What the layer guarantees:

* **A task is a unit of work, not a model call.** A task keeps cycling
  (analyse -> inspect -> implement -> run -> fix -> verify) until its acceptance
  criteria are met, its attempt budget runs out, or it is genuinely blocked.
* **A model response is not proof.** Completion is decided by
  :func:`seedcode.core.tasks.verify_task` from observed evidence (files
  changed, commands that ran, tests that passed, unresolved errors).
* **Limits do not end the task.** A turn that stops because of an output or
  context limit sets ``TurnEvidence.incomplete`` and the session simply issues
  another model call with a compact, rebuilt context.
* **Recovery is bounded and intelligent.** Provider errors retry with backoff;
  a failed command produces a repair cycle with the real error; and a repeated
  identical failure stops with an explicit blocker instead of looping forever.
* **State survives.** Every meaningful transition is persisted as a checkpoint
  in ``.seedcode/checkpoints/``, so a pause, an API failure, or a resumed
  session continues from the current task rather than starting over.
* **The user stays in control.** Cancellation and pausing are checked between
  model calls, tool cycles, and tasks; neither closes applications or touches
  project files beyond what the agent already wrote.

The module contains no provider-specific code: it drives whatever agent object
it is handed (anything implementing ``run_turn(text)`` and exposing
``last_evidence``), which is exactly what the test suite scripts.
"""

from __future__ import annotations

import enum
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..utils.logger import get_logger
from .tasks import (
    RESOLVED_STATES,
    Task,
    TaskGraph,
    TaskState,
    TurnEvidence,
    VerificationOutcome,
    evidence_summary,
    parse_plan,
    verify_task,
)

_log = get_logger("codemode.session")

# --- budgets (safety rails, never a "small call limit") ----------------------
# A single model/tool cycle inside a task uses the agent's own step budget; the
# session keeps issuing cycles until the task verifies. These bounds only stop
# runaway loops, repeated identical failures, and unrecoverable provider errors.
DEFAULT_MAX_TASK_ATTEMPTS = 3        # repair attempts per task after a failure
DEFAULT_MAX_MODEL_CALLS = 400        # whole session
DEFAULT_PROVIDER_RETRIES = 2         # retries for a failed provider request
DEFAULT_BACKOFF_S = 1.0
DEFAULT_RECONNECT_ATTEMPTS = 3       # extra tries when the connection is lost
DEFAULT_RECONNECT_BACKOFF_S = 2.0    # first reconnect delay (grows, capped)
REPEAT_LIMIT = 2                     # identical non-progress fingerprints allowed

#: "TASK <id> SKIPPED: <reason>" — the model may declare a task genuinely not
#: needed; it is recorded as skipped, never as completed work.
_SKIP_RE = re.compile(
    r"TASK\s+(\d+)\s+SKIPPED\b\s*[:\-–]?\s*(.*)", re.IGNORECASE
)


class TaskCancelled(Exception):
    """The user asked to stop the session; state and files are preserved."""


class SessionPaused(Exception):
    """The user asked to pause; the session stops at a clean task boundary."""


class SessionProviderError(Exception):
    """The provider failed after its bounded retries were exhausted."""


class SessionConnectivityError(SessionProviderError):
    """A retryable failure (network/rate-limit/server) that did not recover.

    Distinct from a permanent provider error: the task did not fail, the
    *connection* did. The session pauses with its state and checkpoint intact
    instead of reporting the task as failed, and the work resumes from the
    same point once the connection is back.
    """


class SessionLimitError(Exception):
    """A safety rail (model-call budget) was reached."""


# --- control -----------------------------------------------------------------
@dataclass
class ControlFlags:
    """Cancellation and pause switches shared with the CLI.

    The session checks them between model calls, tool cycles, and tasks, so a
    request made while a turn is running takes effect at the next safe point.
    """

    cancel_requested: bool = False
    pause_requested: bool = False

    def request_cancel(self) -> None:
        self.cancel_requested = True

    def request_pause(self) -> None:
        self.pause_requested = True

    def clear(self) -> None:
        self.cancel_requested = False
        self.pause_requested = False

    def check(self) -> None:
        """Raise when a request must take effect right now."""
        if self.cancel_requested:
            raise TaskCancelled("cancelled by the user")
        if self.pause_requested:
            raise SessionPaused("paused by the user")


@enum.unique
class SessionStatus(str, enum.Enum):
    """Where a Code Mode session is (surfaced by the compact header)."""

    IDLE = "idle"
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# --- working state / continuous context --------------------------------------
@dataclass
class WorkingState:
    """The compact working state rebuilt into every model call.

    Deliberately not a transcript: the original request, where the plan stands,
    and the concrete facts (changed files, commands, tests, errors, blockers)
    that the next call needs. Raw history is summarized by the agent layer, so
    context growth stays bounded while nothing important is lost.
    """

    request: str = ""
    workspace: str = ""
    current_task: str = ""
    completed: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    dependencies: dict[str, list[int]] = field(default_factory=dict)
    acceptance: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    inspected: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    attempted_fixes: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    # --- absorb observed activity -------------------------------------------
    def note_evidence(self, evidence: TurnEvidence) -> None:
        for path in evidence.changed_files:
            if path not in self.changed_files:
                self.changed_files.append(path)
        # "Files inspected" is part of the persistent context (§3): what the
        # session already looked at, so a resumed run does not re-read it.
        for target in evidence.inspected:
            if target not in self.inspected:
                self.inspected.append(target)
        for record in evidence.commands:
            label = f"{record.command} -> {'ok' if record.ok else 'FAILED'}"
            if label not in self.commands:
                self.commands.append(label)
            if record.test:
                test_label = f"{record.command}: {'passed' if record.ok else 'failed'}"
                if test_label not in self.tests:
                    self.tests.append(test_label)
        for message in evidence.errors:
            if message not in self.errors:
                self.errors.append(message)
        del self.changed_files[:-40]
        del self.inspected[:-40]
        del self.commands[:-25]
        del self.tests[:-12]
        del self.errors[:-10]

    def note_fix(self, description: str) -> None:
        if description:
            self.attempted_fixes.append(description)
            del self.attempted_fixes[:-8]

    def note_blocker(self, description: str) -> None:
        if description and description not in self.blockers:
            self.blockers.append(description)
            del self.blockers[:-6]

    # --- prompt rendering ----------------------------------------------------
    def to_prompt(self) -> str:
        """The compact ``SESSION STATE`` block prepended to a task prompt."""
        lines = [
            "SESSION STATE (compact, authoritative):",
            f"- Original request: {self.request.strip()[:400]}",
            f"- Workspace: {self.workspace}",
        ]
        if self.current_task:
            lines.append(f"- Current task: {self.current_task}")
        if self.completed:
            lines.append("- Completed: " + "; ".join(self.completed[-6:]))
        if self.remaining:
            lines.append("- Remaining: " + "; ".join(self.remaining[:8]))
        if self.dependencies:
            pairs = ", ".join(
                f"{task}<-{deps}" for task, deps in list(self.dependencies.items())[:8]
            )
            lines.append(f"- Dependencies: {pairs}")
        if self.acceptance:
            lines.append(
                "- Acceptance criteria for this task:\n"
                + "\n".join(f"    * {c}" for c in self.acceptance)
            )
        if self.changed_files:
            lines.append("- Files changed: " + ", ".join(self.changed_files[-12:]))
        if self.inspected:
            lines.append("- Files inspected: " + ", ".join(self.inspected[-12:]))
        if self.commands:
            lines.append("- Commands run: " + "; ".join(self.commands[-6:]))
        if self.tests:
            lines.append("- Tests: " + "; ".join(self.tests[-4:]))
        if self.errors:
            lines.append("- Known errors: " + "; ".join(self.errors[-3:]))
        if self.attempted_fixes:
            lines.append("- Attempted fixes: " + "; ".join(self.attempted_fixes[-4:]))
        if self.blockers:
            lines.append("- Blockers: " + "; ".join(self.blockers[-3:]))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "workspace": self.workspace,
            "current_task": self.current_task,
            "completed": list(self.completed),
            "remaining": list(self.remaining),
            "dependencies": {str(k): list(v) for k, v in self.dependencies.items()},
            "acceptance": list(self.acceptance),
            "changed_files": list(self.changed_files),
            "inspected": list(self.inspected),
            "commands": list(self.commands),
            "tests": list(self.tests),
            "errors": list(self.errors),
            "attempted_fixes": list(self.attempted_fixes),
            "blockers": list(self.blockers),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkingState":
        return cls(
            request=str(data.get("request", "")),
            workspace=str(data.get("workspace", "")),
            current_task=str(data.get("current_task", "")),
            completed=[str(x) for x in data.get("completed") or []],
            remaining=[str(x) for x in data.get("remaining") or []],
            dependencies={
                str(k): [int(v) for v in vals]
                for k, vals in (data.get("dependencies") or {}).items()
            },
            acceptance=[str(x) for x in data.get("acceptance") or []],
            changed_files=[str(x) for x in data.get("changed_files") or []],
            inspected=[str(x) for x in data.get("inspected") or []],
            commands=[str(x) for x in data.get("commands") or []],
            tests=[str(x) for x in data.get("tests") or []],
            errors=[str(x) for x in data.get("errors") or []],
            attempted_fixes=[str(x) for x in data.get("attempted_fixes") or []],
            blockers=[str(x) for x in data.get("blockers") or []],
        )


# --- prompts -----------------------------------------------------------------
_PLAN_PROMPT = """\
Turn the user's request into an implementation plan for THIS repository.

Do not write any code in this step. Inspect the project (list_dir, read_file,
search_text) if you need to, then reply with a plan block and nothing else:

```plan
{"tasks": [
  {"title": "short imperative title",
   "detail": "what this task changes and where",
   "acceptance": ["file: path/to/file.py", "run: pytest -q", "no-errors"],
   "depends_on": []},
  ...
]}
```

Rules that make the plan executable:
- 3-8 tasks, in dependency order; a dependent task lists its prerequisite ids
  in "depends_on" (ids are the 1-based positions in this list).
- "acceptance" entries must be machine-checkable where possible:
    * "file: <relative path>"                - the file exists
    * "file: <path> | contains: <text>"      - it exists and contains the text
    * "run: <command fragment>"              - that command ran and succeeded
    * "tests"                                - a test run happened and passed
    * "no-errors"                            - no unresolved error remains
  Only use free-text criteria when the outcome genuinely cannot be checked;
  they still require concrete progress.
- Include a final task that runs the project's tests/build when the project has
  them.
- Finish the plan with the last line: "Task 1 is next."
"""

_TASK_INSTRUCTION = """\
Implement TASK {task_id} of {total}: {title}

{detail}

This task is NOT complete when you say it is complete. It is complete when the
acceptance criteria below are satisfied by real project evidence (files that
exist, commands that ran successfully, tests that passed, no unresolved error):

{criteria}

Work in cycles: inspect the existing code, make the change, run the relevant
command/test, read the result, fix what fails, re-run, then confirm. Never
rewrite a file you have not read. Keep every edit inside the workspace.
When the criteria are genuinely met, finish your reply with exactly:
TASK {task_id} COMPLETE
If you cannot finish in this response, say exactly:
CONTINUE TASK {task_id}<newline>NEXT ACTION: <the very next concrete action>
"""

_REPAIR_INSTRUCTION = """\
TASK {task_id} ("{title}") was NOT verified. Do not claim it is finished.

Unmet acceptance criteria:
{unmet}

Observed failures from this task:
{failures}

Fix the underlying cause now: read the failing output, change the code, re-run
the command or test, and confirm it passes. Stay inside the workspace and keep
the change minimal. When the criteria really pass, end your reply with exactly:
TASK {task_id} COMPLETE
"""

_FINAL_VERIFICATION_PROMPT = """\
Every planned task is reported complete. Now run FINAL VERIFICATION for the
whole request, using the project's own tools:

- run the test suite (if the project has one),
- run the build / type check / lint the project supports,
- start the application or smoke-test the changed entry point where practical,
- re-read the changed files and confirm the original request is satisfied.

Report the exact commands you ran and their results. If something fails, fix it
now and re-run until it passes. When everything passes, end with exactly:
FINAL VERIFICATION PASSED
"""


# --- the session -------------------------------------------------------------
class CodeSession:
    """Drives one Code Mode request to verified completion, task by task."""

    def __init__(
        self,
        agent: Any,
        *,
        workspace: Path,
        request: str,
        store: Any = None,
        control: ControlFlags | None = None,
        graph: TaskGraph | None = None,
        state: WorkingState | None = None,
        on_event: Callable[[str, str], None] | None = None,
        max_task_attempts: int = DEFAULT_MAX_TASK_ATTEMPTS,
        max_model_calls: int = DEFAULT_MAX_MODEL_CALLS,
        provider_retries: int = DEFAULT_PROVIDER_RETRIES,
        backoff_s: float = DEFAULT_BACKOFF_S,
        reconnect_attempts: int = DEFAULT_RECONNECT_ATTEMPTS,
        reconnect_backoff_s: float = DEFAULT_RECONNECT_BACKOFF_S,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.agent = agent
        self.workspace = Path(workspace)
        self.request = request.strip()
        self.store = store
        self.control = control or ControlFlags()
        self.graph = graph or TaskGraph(request=self.request)
        self.state = state or WorkingState(
            request=self.request, workspace=str(self.workspace)
        )
        self._on_event_impl = on_event
        self.max_task_attempts = max(1, int(max_task_attempts))
        self.max_model_calls = max(1, int(max_model_calls))
        self.provider_retries = max(0, int(provider_retries))
        self.backoff_s = max(0.0, float(backoff_s))
        self.reconnect_attempts = max(0, int(reconnect_attempts))
        self.reconnect_backoff_s = max(0.0, float(reconnect_backoff_s))
        self._sleep = sleep or time.sleep
        # Bookkeeping surfaced by the header and the checkpoint.
        self.status = SessionStatus.IDLE
        self.model_calls = 0
        self.tool_calls = 0
        self.recoveries = 0
        self.started_at = time.monotonic()
        self.reason = ""
        # Evidence for the task in flight and for the whole session.
        self.task_evidence = TurnEvidence()
        self.session_evidence = TurnEvidence()
        self._seen_failures: dict[str, int] = {}

    # --- public API ----------------------------------------------------------
    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def set_observer(self, on_event: Callable[[str, str], None] | None) -> None:
        """Replace the progress observer (used when a session is resumed)."""
        self._on_event_impl = on_event

    def event(self, kind: str, detail: str = "") -> None:
        """Report progress (a UI bug must never break the session)."""
        try:
            if self._on_event_impl is not None:
                self._on_event_impl(kind, detail)
        except Exception:
            _log.exception("session observer failed")

    # --- the persistent loop -------------------------------------------------
    def run(self) -> SessionStatus:
        """Run until the project is verified complete, paused, or stopped."""
        if self.control.cancel_requested:
            self.status = SessionStatus.CANCELLED
            self.reason = "cancelled by the user — no work was started"
            self._cancel_open_tasks()
            return self.status
        try:
            if not self.graph.tasks:
                self._plan()
            self.status = SessionStatus.RUNNING
            self._work_loop()
            if self.graph.all_completed():
                self._final_verification()
            if self.graph.all_completed() and not self._final_problem():
                self._complete()
            else:
                self.status = SessionStatus.FAILED
                self.reason = self.reason or "tasks remain unfinished"
        except KeyboardInterrupt:
            # Ctrl+C at the CLI: stop the autonomous session, keep every file
            # and the checkpoint, and never close an application.
            self.control.request_cancel()
            self.status = SessionStatus.CANCELLED
            self.reason = "cancelled by the user"
            self._cancel_open_tasks()
        except SessionPaused:
            self.status = SessionStatus.PAUSED
            self.reason = "paused — send /resume to continue"
        except TaskCancelled:
            self.status = SessionStatus.CANCELLED
            self.reason = "cancelled by the user"
            self._cancel_open_tasks()
        except SessionConnectivityError as exc:
            # The connection died, not the task: pause safely with the state
            # and checkpoint intact so nothing is lost and the work resumes
            # from the same task instead of failing.
            self.status = SessionStatus.PAUSED
            self.reason = (
                f"connection lost — {exc}. Task state, files and the "
                "checkpoint were kept; send /resume once the connection is "
                "back."
            )
            self.event("network_lost", str(exc))
        except SessionProviderError as exc:
            self.status = SessionStatus.FAILED
            self.reason = str(exc)
            self.event("error", str(exc))
        except SessionLimitError as exc:
            self.status = SessionStatus.FAILED
            self.reason = str(exc)
            self.event("error", str(exc))
        if self.status is not SessionStatus.COMPLETED:
            # A finished session has nothing to resume: its checkpoint was
            # cleared by _complete() and must not be written again.
            self._checkpoint()
        self.event(f"session_{self.status.value}", self.reason)
        return self.status

    # --- planning ------------------------------------------------------------
    def _plan(self) -> None:
        self.status = SessionStatus.PLANNING
        self.event("plan", self.request[:120])
        reply = self._call(f"{_PLAN_PROMPT}\nUSER REQUEST:\n{self.request}")
        items = parse_plan(_plan_text(reply, self.agent))
        if items:
            self.graph = TaskGraph.from_plan(self.request, items)
        else:
            # The model produced no usable plan: fall back to one honest task
            # rather than pretending a plan exists.
            self.graph = TaskGraph.single(self.request)
        self._reset_running_tasks()
        self._save_plan()
        self._checkpoint()
        self.event(
            "plan_ready",
            f"{self.graph.total} task(s): "
            + "; ".join(t.title[:40] for t in self.graph.tasks[:6]),
        )

    def _reset_running_tasks(self) -> None:
        """A resumed session restarts the task that was mid-flight."""
        for task in self.graph.tasks:
            if task.state in (TaskState.RUNNING, TaskState.VERIFYING, TaskState.RECOVERING):
                task.mark(TaskState.PENDING, "re-queued after resume")
                task.current_action = ""  # it is not running right now

    # --- the task loop -------------------------------------------------------
    def _work_loop(self) -> None:
        while not self.graph.all_completed():
            self.control.check()
            # A pending task whose prerequisite can never finish is not
            # runnable: mark it skipped (with the reason) instead of leaving
            # it dangling, so the plan reflects reality.
            self._skip_unrunnable_tasks()
            task = self.graph.next_task()
            if task is None:
                # Nothing runnable: every remaining task is blocked or failed.
                pending = self.graph.unresolved()
                if not any(t.state is TaskState.PENDING for t in pending):
                    # Keep a task-specific reason (set when it failed) rather
                    # than flattening it into a generic message.
                    self.reason = self.reason or (
                        "no runnable task remains ("
                        + "; ".join(
                            f"{t.id}: {t.state.value}" for t in pending[:3]
                        )
                        + ")"
                    )
                    return
                # A dependency is stuck; report it instead of looping.
                self.reason = "remaining tasks are waiting on unfinished dependencies"
                return
            if not self._run_task(task):
                if self.status in (SessionStatus.PAUSED, SessionStatus.CANCELLED):
                    return
                # A failed/blocked task ends the loop; the checkpoint keeps it.
                self._skip_unrunnable_tasks()
                self._checkpoint()
                return

    def _skip_unrunnable_tasks(self) -> int:
        """Skip pending tasks whose prerequisites cannot complete.

        A task is never silently dropped: it is marked ``SKIPPED`` with the
        prerequisite that did not finish, transitively (a task depending on a
        skipped task is skipped too). Returns how many were skipped.
        """
        blocked_deps = {
            TaskState.FAILED,
            TaskState.BLOCKED,
            TaskState.SKIPPED,
            TaskState.CANCELLED,
        }
        skipped = 0
        changed = True
        while changed:
            changed = False
            for task in self.graph.tasks:
                if task.state is not TaskState.PENDING:
                    continue
                for dep_id in task.depends_on:
                    dep = self.graph.get(dep_id)
                    if dep is None or dep.state not in blocked_deps:
                        continue
                    note = (
                        f"skipped: prerequisite task {dep.id} is "
                        f"{dep.state.value}"
                    )
                    task.finish(TaskState.SKIPPED, note)
                    skipped += 1
                    changed = True
                    self.event(
                        "task_skipped",
                        f"Task {task.id} skipped — prerequisite task "
                        f"{dep.id} did not complete",
                    )
                    break
        if skipped:
            self._save_plan()
        return skipped

    def _run_task(self, task: Task) -> bool:
        """Drive one task to verified completion. Returns whether it finished."""
        self.task_evidence = TurnEvidence()
        task.begin()  # RUNNING + start timestamp + current action
        self._sync_state(task)
        self._checkpoint()
        self.event("task_start", f"Task {task.id}/{self.graph.total}: {task.title}")
        attempts = 0
        last_outcome: VerificationOutcome | None = None
        prompt = _TASK_INSTRUCTION.format(
            task_id=task.id,
            total=self.graph.total,
            title=task.title,
            detail=task.detail or "(no extra detail)",
            criteria=_criteria_text(task),
        )

        while True:
            self.control.check()
            task.note_action("working")
            prompt = f"{self.state.to_prompt()}\n\n{prompt}"
            reply = self._call(prompt)
            self._absorb(reply)
            # Mirror this task's observed evidence onto the task itself, so the
            # record (files affected, commands, tool calls, tests, errors) is
            # per task and survives a checkpoint resume.
            task.absorb(self.task_evidence)
            # The model may declare a task genuinely not needed. That is an
            # explicit, recorded skip — never quietly counted as completed
            # work (see TaskGraph.all_completed).
            skip = _SKIP_RE.search(reply or "")
            if skip is not None and int(skip.group(1)) == task.id:
                reason = " ".join((skip.group(2) or "").split())[:200]
                reason = reason or "declared not needed"
                task.finish(TaskState.SKIPPED, f"skipped: {reason}")
                self._save_plan()
                self._checkpoint()
                self.event("task_skipped", f"Task {task.id} skipped: {reason}")
                return True
            task.note_action("verifying acceptance criteria")
            task.mark(TaskState.VERIFYING, "verifying acceptance criteria")
            self.event("verify", f"verifying Task {task.id}")
            outcome = verify_task(task, self.task_evidence, self.workspace)
            last_outcome = outcome
            task.verification = outcome.detail
            if outcome.ok:
                task.finish(TaskState.COMPLETED, f"verified: {outcome.detail}", outcome.detail)
                self._sync_state(task)
                self._save_plan()
                self._checkpoint()
                self.event(
                    "task_done",
                    f"Task {task.id}/{self.graph.total} completed — {outcome.detail} "
                    f"• {evidence_summary(task)}",
                )
                return True

            task.mark(TaskState.RUNNING, "verification failed; continuing")
            fingerprint = f"{task.id}:{self.task_evidence.fingerprint()}"
            count = self._seen_failures.get(fingerprint, 0) + 1
            self._seen_failures[fingerprint] = count

            # A turn that ended on an output/context limit is not a failure:
            # the task simply continues with another model call.
            if self.task_evidence.incomplete and attempts < self.max_task_attempts:
                self.event("continue", f"Task {task.id}: continuing after a limit")
                prompt = _TASK_INSTRUCTION.format(
                    task_id=task.id,
                    total=self.graph.total,
                    title=task.title,
                    detail=task.detail or "(no extra detail)",
                    criteria=_criteria_text(task),
                )
                continue

            if count > REPEAT_LIMIT:
                # The same action produced the same failure twice: stop and
                # explain the blocker instead of looping forever.
                blocker = (
                    f"Task {task.id} is blocked: the same failure repeated "
                    f"({outcome.unmet[0] if outcome.unmet else 'no progress'}). "
                    "It needs a decision from the user."
                )
                task.finish(TaskState.BLOCKED, blocker, outcome.detail)
                self.state.note_blocker(blocker)
                self._checkpoint()
                self.event("task_blocked", blocker)
                self.reason = blocker
                return False

            attempts += 1
            task.attempts = attempts
            if attempts > self.max_task_attempts:
                task.finish(
                    TaskState.FAILED,
                    f"unverified after {attempts} attempts",
                    outcome.detail,
                )
                self._checkpoint()
                self.event(
                    "task_failed",
                    f"Task {task.id} could not be verified: {outcome.detail}",
                )
                self.reason = f"Task {task.id} failed verification: {outcome.detail}"
                return False

            self.recoveries += 1
            task.note_action(f"repairing (attempt {attempts})")
            self.event("recovery", f"Task {task.id}: repairing (attempt {attempts})")
            self.state.note_fix(
                f"attempt {attempts}: " + (outcome.unmet[0] if outcome.unmet else "retry")
            )
            self._checkpoint()
            prompt = _REPAIR_INSTRUCTION.format(
                task_id=task.id,
                title=task.title,
                unmet=outcome.unmet_prompt(),
                failures=_failure_text(last_outcome, self.task_evidence),
            )

    # --- final verification --------------------------------------------------
    def _final_verification(self) -> None:
        self.control.check()
        self.status = SessionStatus.RUNNING
        self.event("final_verify", "running final verification")
        for attempt in range(1, self.max_task_attempts + 1):
            self.control.check()
            prompt = f"{self.state.to_prompt()}\n\n{_FINAL_VERIFICATION_PROMPT}"
            reply = self._call(prompt)
            self._absorb(reply)
            problem = self._final_problem()
            if problem is None:
                self.event("final_ok", "final verification passed")
                return
            self.event("recovery", f"final verification problem: {problem}")
            self.state.note_fix(f"final verification: {problem[:120]}")
            recovery = self.graph.add_recovery_task(
                title=f"Fix final verification problem: {problem[:80]}",
                detail=(
                    "Final verification failed. Fix the problem, re-run the "
                    "verification command, and leave the project passing."
                ),
                acceptance=["no-errors"],
            )
            self.reason = ""
            self._checkpoint()
            if not self._run_task(recovery):
                return
            if attempt == self.max_task_attempts:
                self.reason = f"final verification still failing: {problem}"

    def _final_problem(self) -> str | None:
        """What final verification still disagrees with, if anything."""
        evidence = self.session_evidence
        tasks_left = [
            t for t in self.graph.tasks if t.state not in RESOLVED_STATES
        ]
        if tasks_left:
            return f"{len(tasks_left)} task(s) not completed"
        if evidence.errors:
            return f"unresolved error: {evidence.errors[-1][:160]}"
        tests = evidence.tests
        if tests and not tests[-1].ok:
            return f"last test run failed: {tests[-1].command[:100]}"
        failed_commands = [c for c in evidence.commands if not c.ok]
        if failed_commands and not any(c.ok for c in evidence.commands):
            return f"last command failed: {failed_commands[-1].command[:100]}"
        return None

    # --- completion ----------------------------------------------------------
    def _complete(self) -> None:
        self.status = SessionStatus.COMPLETED
        self.reason = ""
        done, total = self.graph.progress()
        self.event("complete", f"{done}/{total} tasks completed and verified")
        if self.store is not None:
            try:
                self.store.clear_checkpoint()
            except Exception:
                pass

    def _cancel_open_tasks(self) -> None:
        for task in self.graph.tasks:
            if task.state in (
                TaskState.PENDING,
                TaskState.RUNNING,
                TaskState.VERIFYING,
                TaskState.RECOVERING,
            ):
                task.finish(TaskState.CANCELLED, "cancelled by the user")

    # --- model calls ---------------------------------------------------------
    def _call(self, prompt: str) -> str:
        """One model call with bounded, backed-off provider retries.

        Transient failures (a dropped connection, a timeout, a 429/5xx) get a
        second, longer reconnect phase before the call is given up on. A
        non-transient failure (bad key, unknown model, invalid request) is
        permanent and never enters that phase. When even reconnection fails,
        the difference is preserved: a connectivity loss raises
        :class:`SessionConnectivityError` (safe pause), a real provider error
        raises :class:`SessionProviderError` (a genuine failure).
        """
        from .chat import ChatError

        self.control.check()
        if self.model_calls >= self.max_model_calls:
            raise SessionLimitError(
                f"session model-call budget reached ({self.max_model_calls}); "
                "progress is checkpointed and can be resumed."
            )
        last: ChatError | None = None
        delay = self.backoff_s
        for attempt in range(self.provider_retries + 1):
            try:
                reply = self.agent.run_turn(prompt)
                self.model_calls += 1
                return reply or ""
            except ChatError as exc:
                last = exc
                if attempt >= self.provider_retries:
                    break
                _log.warning("provider call failed (%s); retrying in %.1fs", exc, delay)
                self.event("retry", f"provider error, retrying in {delay:g}s")
                if delay:
                    self._sleep(delay)
                delay = min(delay * 2 if delay else 0.0, 8.0)

        if last is not None and _transient_failure(last):
            # Network / rate-limit / server problem: try to reconnect rather
            # than ending the task. The same call is retried, so no work (and
            # no context) is lost while the connection is down.
            reconnect_delay = self.reconnect_backoff_s
            for attempt in range(1, self.reconnect_attempts + 1):
                self.control.check()
                self.event(
                    "reconnect",
                    f"connection lost — reconnecting "
                    f"(attempt {attempt}/{self.reconnect_attempts})",
                )
                if reconnect_delay:
                    self._sleep(reconnect_delay)
                try:
                    reply = self.agent.run_turn(prompt)
                    self.model_calls += 1
                    self.event("reconnected", "connection restored — resuming")
                    return reply or ""
                except ChatError as exc:
                    last = exc
                    if not _transient_failure(exc):
                        break
                reconnect_delay = min(reconnect_delay * 2 if reconnect_delay else 0.0, 16.0)
            raise SessionConnectivityError(
                f"the connection to the provider was lost after "
                f"{self.provider_retries + 1} retry(ies) and "
                f"{self.reconnect_attempts} reconnect attempt(s): {last}"
            ) from last

        raise SessionProviderError(
            f"provider request failed after "
            f"{self.provider_retries + 1} attempt(s): {last}"
        ) from last

    def _absorb(self, reply: str) -> None:
        """Fold the cycle's observed evidence into task/session/working state."""
        evidence = getattr(self.agent, "last_evidence", None)
        if not isinstance(evidence, TurnEvidence):
            evidence = TurnEvidence()
        evidence.model_calls += 1
        if reply:
            evidence.summary = reply.strip()[-400:]
        self.task_evidence.merge(evidence)
        self.session_evidence.merge(evidence)
        self.tool_calls += getattr(evidence, "tools", 0)
        self.state.note_evidence(evidence)

    # --- state helpers -------------------------------------------------------
    def _sync_state(self, task: Task | None) -> None:
        self.state.request = self.request
        self.state.workspace = str(self.workspace)
        self.state.completed = [
            f"{t.id}. {t.title}" for t in self.graph.tasks if t.state is TaskState.COMPLETED
        ]
        # Skipped tasks are resolved, not outstanding work: they must not read
        # as "remaining" to the next model call.
        self.state.remaining = [
            f"{t.id}. {t.title}"
            for t in self.graph.tasks
            if t.state not in RESOLVED_STATES
        ]
        self.state.dependencies = {
            str(t.id): list(t.depends_on) for t in self.graph.tasks if t.depends_on
        }
        self.state.acceptance = list(task.acceptance) if task is not None else []

    # --- persistence ---------------------------------------------------------
    def _save_plan(self) -> None:
        if self.store is None:
            return
        try:
            self.store.save_plan(self.graph.to_dict())
        except Exception:
            _log.exception("could not save the Code Mode plan")

    def checkpoint(self) -> dict[str, Any]:
        """The resumable state of this session (never a raw transcript)."""
        active = self.graph.active() or self.graph.next_task()
        done, total = self.graph.progress()
        return {
            "version": 1,
            "status": self.status.value,
            "request": self.request[:2000],
            "workspace": str(self.workspace),
            "plan": self.graph.to_dict(),
            "current_task": (
                {
                    "id": active.id,
                    "title": active.title,
                    "state": active.state.value,
                }
                if active is not None
                else None
            ),
            "progress": {"completed": done, "total": total},
            "changed_files": list(self.state.changed_files),
            "discoveries": list(self.state.inspected),
            "commands": list(self.state.commands),
            "errors": list(self.state.errors),
            "tests": list(self.state.tests),
            "attempted_fixes": list(self.state.attempted_fixes),
            "blockers": list(self.state.blockers),
            "next_action": (
                f"continue Task {active.id}: {active.title}"
                if active is not None
                else "run final verification"
            ),
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "reason": self.reason,
        }

    def _checkpoint(self) -> None:
        if self.store is None:
            return
        try:
            self.store.save_checkpoint(self.checkpoint())
        except Exception:
            _log.exception("could not save the Code Mode checkpoint")

    @classmethod
    def resume(
        cls,
        agent: Any,
        *,
        store: Any,
        workspace: Path,
        on_event: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> "CodeSession | None":
        """Rebuild the session from its checkpoint (None when there is none)."""
        data = None
        try:
            data = store.load_checkpoint()
        except Exception:
            data = None
        if not data or str(data.get("status")) == SessionStatus.COMPLETED.value:
            return None  # nothing to resume (a finished session leaves none)
        graph = TaskGraph.from_dict(data.get("plan") or {})
        state = WorkingState.from_dict(
            {
                "request": data.get("request", ""),
                "workspace": data.get("workspace", str(workspace)),
                "changed_files": data.get("changed_files") or [],
                "inspected": data.get("discoveries") or [],
                "commands": data.get("commands") or [],
                "tests": data.get("tests") or [],
                "errors": data.get("errors") or [],
                "attempted_fixes": data.get("attempted_fixes") or [],
                "blockers": data.get("blockers") or [],
            }
        )
        session = cls(
            agent,
            workspace=workspace,
            request=str(data.get("request", "")) or state.request,
            store=store,
            graph=graph,
            state=state,
            on_event=on_event,
            **kwargs,
        )
        session.model_calls = int(data.get("model_calls", 0))
        session.tool_calls = int(data.get("tool_calls", 0))
        session._reset_running_tasks()
        return session


# --- helpers -----------------------------------------------------------------
def _transient_failure(exc: BaseException) -> bool:
    """Whether a ChatError wraps a retryable provider failure.

    Providers mark timeouts, connection drops, rate limits and 5xx responses
    ``transient=True`` on the underlying :class:`ProviderError`; the chat
    engine always raises ``ChatError`` *from* that error, so the cause carries
    the classification. An error with no such cause (a permanent failure, or a
    bare ``ChatError``) is not treated as connectivity.
    """
    return bool(getattr(getattr(exc, "__cause__", None), "transient", False))


def _plan_text(reply: str, agent: Any) -> str:
    """The plan, taken from the reply or the last assistant message."""
    if parse_plan(reply):
        return reply
    try:
        for message in reversed(getattr(agent, "messages", []) or []):
            if getattr(message, "role", "") == "assistant" and getattr(message, "content", ""):
                if parse_plan(message.content):
                    return message.content
    except Exception:
        pass
    return reply


def _criteria_text(task: Task) -> str:
    if not task.acceptance:
        return (
            "- no explicit criteria: real progress (files changed, a command "
            "succeeding) must be observable and no unresolved error may remain"
        )
    return "\n".join(f"- {c}" for c in task.acceptance)


def _failure_text(
    outcome: VerificationOutcome | None, evidence: TurnEvidence
) -> str:
    lines: list[str] = []
    if outcome is not None and outcome.unmet:
        lines.extend(f"- unmet: {item}" for item in outcome.unmet[:4])
    lines.extend(f"- error: {item}" for item in evidence.errors[-3:])
    failed = [c for c in evidence.commands if not c.ok]
    lines.extend(
        f"- failed command: {c.command[:120]}" for c in failed[-3:]
    )
    return "\n".join(lines) or "- no output was captured; re-run the failing step"


# --- the current session (process-wide, used by /pause /resume /stop) --------
_current: CodeSession | None = None


def current_session() -> CodeSession | None:
    """The Code Mode session the CLI is driving, if any."""
    return _current


def set_current_session(session: CodeSession | None) -> None:
    global _current
    _current = session


def reset() -> None:
    """Test isolation hook."""
    global _current
    _current = None


def pause(session: CodeSession | None = None) -> bool:
    """Ask a running session to pause at the next safe point."""
    target = session or _current
    if target is None:
        return False
    target.control.request_pause()
    return True


def stop(session: CodeSession | None = None) -> bool:
    """Ask a running session to stop; state and files are preserved."""
    target = session or _current
    if target is None:
        return False
    target.control.request_cancel()
    return True


def resume(agent: Any, *, workspace: Path, store: Any, **kwargs: Any) -> CodeSession | None:
    """Rebuild a paused/failed session from its checkpoint."""
    return CodeSession.resume(agent, store=store, workspace=workspace, **kwargs)


__all__ = [
    "CodeSession",
    "ControlFlags",
    "DEFAULT_MAX_MODEL_CALLS",
    "DEFAULT_MAX_TASK_ATTEMPTS",
    "SessionConnectivityError",
    "SessionLimitError",
    "SessionPaused",
    "SessionProviderError",
    "SessionStatus",
    "TaskCancelled",
    "WorkingState",
    "current_session",
    "pause",
    "reset",
    "resume",
    "set_current_session",
    "stop",
]
