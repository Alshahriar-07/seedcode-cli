"""Task execution engine (v7.1.0): a task is a unit of work, not a model call.

Code Mode used to be "one user request -> one agent turn": the model answered,
the turn ended, and whatever had not been done simply was not done. This module
is the data model that makes a *task* the unit of work instead:

* a :class:`Task` is a unit of work with explicit acceptance criteria and
  optional dependencies;
* a :class:`TaskGraph` is the plan: the ordered TODOs, each with a real state,
  and a scheduler that activates the next runnable task (dependencies first);
* :class:`TurnEvidence` records what actually happened during a model/tool
  cycle (files changed, commands run, tests run, errors), so completion can be
  decided from project evidence instead of from the model's own claim;
* :func:`verify_task` checks a task's acceptance criteria against that
  evidence — ``"Done"`` in a model response is never proof of completion.

The module is pure, offline, and free of AI code, so it is trivially testable.
"""

from __future__ import annotations

import enum
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# --- states ------------------------------------------------------------------
class TaskState(str, enum.Enum):
    """The lifecycle of one task in the plan.

    A task is only ``COMPLETED`` after verification (`PENDING -> RUNNING ->
    VERIFYING -> COMPLETED`). Failures are recoverable: `RUNNING -> FAILED ->
    RECOVERING -> RUNNING`. A task that cannot proceed (`BLOCKED`) stays
    visible with its blocker instead of silently disappearing.
    """

    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    BLOCKED = "blocked"
    RECOVERING = "recovering"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


#: States where the task will not be run again.
TERMINAL_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
)

#: The checklist glyph for each state (UI layer reads these).
GLYPHS: dict[TaskState, str] = {
    TaskState.PENDING: "○",
    TaskState.RUNNING: "●",
    TaskState.VERIFYING: "●",
    TaskState.BLOCKED: "■",
    TaskState.RECOVERING: "●",
    TaskState.FAILED: "✗",
    TaskState.COMPLETED: "✓",
    TaskState.CANCELLED: "■",
}


def glyph_for(state: TaskState, *, legacy: bool = False) -> str:
    """Checklist mark for ``state`` (ASCII on legacy consoles)."""
    if legacy:
        return {
            TaskState.PENDING: "-",
            TaskState.RUNNING: ">",
            TaskState.VERIFYING: ">",
            TaskState.BLOCKED: "[!]",
            TaskState.RECOVERING: ">",
            TaskState.FAILED: "[x]",
            TaskState.COMPLETED: "[ok]",
            TaskState.CANCELLED: "[!]",
        }[state]
    return GLYPHS[state]


# --- one task ----------------------------------------------------------------
_CRITERION_FILE = "file"
_CRITERION_RUN = "run"
_CRITERION_TESTS = "tests"
_CRITERION_NO_ERRORS = "no-errors"


@dataclass
class Task:
    """One planned unit of work.

    Besides its plan data (title, detail, dependencies, acceptance criteria),
    a task keeps its own record of what actually happened while it ran — the
    current action, the files it inspected and affected, the commands it ran
    with their outcome, its tool-call count, its test results, the errors it
    hit, its retry count, its timestamps and its final verification result
    (v7.1.0). That record is what the checkpoint persists, so a resumed session
    knows per task what was already done instead of only a session-wide list.
    """

    id: int
    title: str
    detail: str = ""
    acceptance: list[str] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)
    state: TaskState = TaskState.PENDING
    attempts: int = 0
    notes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    blocker: str = ""
    # --- the task's own execution record (v7.1.0) --------------------------
    current_action: str = ""
    inspected: list[str] = field(default_factory=list)
    files_affected: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    tool_calls: int = 0
    tests: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    verification: str = ""

    # --- scheduling ----------------------------------------------------------
    def ready(self, completed: set[int]) -> bool:
        """Whether this task may start now (dependencies satisfied)."""
        if self.state is not TaskState.PENDING:
            return False
        return all(dep in completed for dep in self.depends_on)

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def duration_s(self) -> float:
        """How long the task ran, or 0 while it has not started/finished."""
        if not self.started_at:
            return 0.0
        return max(0.0, (self.finished_at or time.time()) - self.started_at)

    # --- mutation ------------------------------------------------------------
    def mark(self, state: TaskState, note: str = "") -> None:
        """Move the task to ``state``, keeping an audit note."""
        self.state = state
        if note:
            self.notes.append(note)
            del self.notes[:-6]  # keep the trail short
        if state is TaskState.BLOCKED:
            self.blocker = note or self.blocker

    def begin(self, note: str = "started") -> None:
        """Start the task: mark it running and stamp the start time."""
        self.mark(TaskState.RUNNING, note)
        self.started_at = time.time()
        self.finished_at = None
        self.current_action = "working"

    def note_action(self, action: str) -> None:
        """Record what the task is doing right now (shown by the header)."""
        self.current_action = " ".join(str(action or "").split())[:160]

    def finish(
        self, state: TaskState, note: str = "", verification: str = ""
    ) -> None:
        """End the task in a terminal state, stamping its verification result."""
        if verification:
            self.verification = verification
        self.mark(state, note)
        self.current_action = ""
        # Only a task that really ran gets an end time (a task cancelled while
        # still pending never started).
        if self.started_at and not self.finished_at:
            self.finished_at = time.time()

    def absorb(self, evidence: TurnEvidence) -> None:
        """Mirror observed evidence onto this task (idempotent, additive).

        The session accumulates one :class:`TurnEvidence` per task, so mirroring
        it after every cycle keeps the task's record complete without ever
        duplicating an entry.
        """
        for path in evidence.inspected:
            if path not in self.inspected:
                self.inspected.append(path)
        for path in evidence.changed_files:
            if path not in self.files_affected:
                self.files_affected.append(path)
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
        self.tool_calls = max(self.tool_calls, int(evidence.tools))
        note = evidence.progress_note()
        if note and (not self.evidence or self.evidence[-1] != note):
            self.evidence.append(note)
        # Keep the record bounded: a long task must not grow without limit.
        del self.inspected[:-40]
        del self.files_affected[:-40]
        del self.commands[:-25]
        del self.tests[:-12]
        del self.errors[:-10]
        del self.evidence[:-10]

    # --- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "detail": self.detail,
            "acceptance": list(self.acceptance),
            "depends_on": list(self.depends_on),
            "state": self.state.value,
            "attempts": self.attempts,
            "notes": list(self.notes),
            "evidence": list(self.evidence),
            "blocker": self.blocker,
            "current_action": self.current_action,
            "inspected": list(self.inspected),
            "files_affected": list(self.files_affected),
            "commands": list(self.commands),
            "tool_calls": self.tool_calls,
            "tests": list(self.tests),
            "errors": list(self.errors),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "verification": self.verification,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        try:
            state = TaskState(str(data.get("state", "pending")))
        except ValueError:
            state = TaskState.PENDING

        def _stamp(key: str) -> float | None:
            try:
                value = data.get(key)
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        return cls(
            id=int(data.get("id", 0)),
            title=str(data.get("title", "")).strip() or "Untitled task",
            detail=str(data.get("detail", "")),
            acceptance=[str(c) for c in data.get("acceptance") or []],
            depends_on=[int(d) for d in data.get("depends_on") or []],
            state=state,
            attempts=int(data.get("attempts", 0)),
            notes=[str(n) for n in data.get("notes") or []],
            evidence=[str(e) for e in data.get("evidence") or []],
            blocker=str(data.get("blocker", "")),
            current_action=str(data.get("current_action", "")),
            inspected=[str(p) for p in data.get("inspected") or []],
            files_affected=[str(p) for p in data.get("files_affected") or []],
            commands=[str(c) for c in data.get("commands") or []],
            tool_calls=int(data.get("tool_calls", 0)),
            tests=[str(t) for t in data.get("tests") or []],
            errors=[str(e) for e in data.get("errors") or []],
            started_at=_stamp("started_at"),
            finished_at=_stamp("finished_at"),
            verification=str(data.get("verification", "")),
        )


@dataclass(slots=True)
class PlanItem:
    """One parsed TODO, before it becomes a :class:`Task`."""

    title: str
    detail: str = ""
    acceptance: list[str] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)


# --- plan parsing ------------------------------------------------------------
_PLAN_BLOCK = re.compile(r"```(?:plan|tasks|todo)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_ACCEPTANCE_RE = re.compile(r"\bacceptance(?:\s+criteria)?\s*[:\-]\s*(.+)$", re.IGNORECASE)
_DEPENDS_RE = re.compile(r"\bdepends(?:\s+on)?\s*[:\-]?\s*([0-9,\s]+)", re.IGNORECASE)


def _split_acceptance(text: str) -> tuple[str, list[str]]:
    """Pull an ``Acceptance: a; b; c`` tail out of a task line."""
    match = _ACCEPTANCE_RE.search(text)
    if match is None:
        return text, []
    criteria = [
        c.strip().strip(":-—–| 	")
        for c in re.split(r"[;|]", match.group(1))
        if c.strip()
    ]
    return text[: match.start()].strip(), [c for c in criteria if c]


def _split_depends(text: str) -> tuple[str, list[int]]:
    """Pull a ``Depends on: 1, 2`` tail out of a task line."""
    match = _DEPENDS_RE.search(text)
    if match is None:
        return text, []
    numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
    return text[: match.start()].strip(), numbers


def parse_plan(text: str) -> list[PlanItem]:
    """Parse the model's plan into TODOs.

    Accepts, in order of preference:

    1. a fenced ```` ```plan ```` block holding JSON
       (``{"tasks": [{"title": ..., "acceptance": [...]}]}``);
    2. a fenced ```` ```plan ```` block holding numbered lines;
    3. a numbered list anywhere in the reply (``1. …``, ``2) …``).

    Anything unparseable yields an empty list — the caller decides the
    fallback (one task equal to the whole request). Parsing never raises.
    """
    if not text:
        return []

    for match in _PLAN_BLOCK.finditer(text):
        body = match.group(1).strip()
        items = _parse_plan_json(body) or _parse_numbered(body)
        if items:
            return items

    return _parse_numbered(text)


def _parse_plan_json(body: str) -> list[PlanItem]:
    try:
        data = json.loads(body)
    except ValueError:
        return []
    if isinstance(data, dict):
        raw_tasks = data.get("tasks") or data.get("plan") or []
    elif isinstance(data, list):
        raw_tasks = data
    else:
        return []
    items: list[PlanItem] = []
    for entry in raw_tasks:
        if isinstance(entry, str):
            title, criteria = _split_acceptance(entry)
            items.append(PlanItem(title=title, acceptance=criteria))
            continue
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or entry.get("task") or "").strip()
        if not title:
            continue
        acceptance = [
            str(c).strip() for c in entry.get("acceptance") or [] if str(c).strip()
        ]
        depends = entry.get("depends_on") or entry.get("depends") or []
        items.append(
            PlanItem(
                title=title,
                detail=str(entry.get("detail") or entry.get("description") or ""),
                acceptance=acceptance,
                depends_on=[int(d) for d in depends if str(d).strip().isdigit()],
            )
        )
    return items


def _parse_numbered(text: str) -> list[PlanItem]:
    """Numbered TODO lines, with optional acceptance / dependency tails."""
    items: list[PlanItem] = []
    for line in text.splitlines():
        match = _NUMBERED.match(line)
        if match is None:
            continue
        body = match.group(2).strip()
        if not body:
            continue
        body, depends = _split_depends(body)
        body, criteria = _split_acceptance(body)
        # Drop any dangling bullet/separator left behind by the tails above
        # ("Add the database layer — Acceptance: …" -> "Add the database layer").
        body = re.sub(r"[\s:—–-]+$", "", body).strip("#* \t")
        if not body:
            continue
        items.append(PlanItem(title=body, acceptance=criteria, depends_on=depends))
    # Guard against swallowing an unrelated numbered list of prose (a summary
    # like "1. Done. 2. Done."): a plan has substance, not one-word items.
    if items and all(len(item.title) < 8 for item in items):
        return []
    return items


# --- the plan ----------------------------------------------------------------
class TaskGraph:
    """The task graph plus its dependency-aware scheduler."""

    def __init__(self, request: str = "", tasks: Iterable[Task] = ()) -> None:
        self.request = request
        self.tasks: list[Task] = list(tasks)

    # --- construction --------------------------------------------------------
    @classmethod
    def from_plan(cls, request: str, items: Iterable[PlanItem]) -> "TaskGraph":
        tasks: list[Task] = []
        for index, item in enumerate(items, start=1):
            tasks.append(
                Task(
                    id=index,
                    title=item.title,
                    detail=item.detail,
                    acceptance=list(item.acceptance),
                    depends_on=[d for d in item.depends_on if 0 < d < index],
                )
            )
        return cls(request=request, tasks=tasks)

    @classmethod
    def single(cls, request: str) -> "TaskGraph":
        """Fallback plan: the whole request as one task, verified end to end."""
        return cls(
            request=request,
            tasks=[
                Task(
                    id=1,
                    title=request.strip()[:120] or "Complete the request",
                    detail=request.strip(),
                    acceptance=["no-errors"],
                )
            ],
        )

    # --- scheduling ----------------------------------------------------------
    def completed_ids(self) -> set[int]:
        return {t.id for t in self.tasks if t.state is TaskState.COMPLETED}

    def next_task(self) -> Task | None:
        """The next runnable task, honouring dependencies (dependency-first)."""
        completed = self.completed_ids()
        for task in self.tasks:
            if task.ready(completed):
                return task
        return None

    def active(self) -> Task | None:
        """The task currently being worked on, if any."""
        for task in self.tasks:
            if task.state in (
                TaskState.RUNNING,
                TaskState.VERIFYING,
                TaskState.RECOVERING,
            ):
                return task
        return None

    def get(self, task_id: int) -> Task | None:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    @property
    def total(self) -> int:
        return len(self.tasks)

    def completed_count(self) -> int:
        return len(self.completed_ids())

    def all_completed(self) -> bool:
        return bool(self.tasks) and all(
            t.state is TaskState.COMPLETED for t in self.tasks
        )

    def unresolved(self) -> list[Task]:
        """Tasks that are not completed (failed, blocked, pending, cancelled)."""
        return [t for t in self.tasks if t.state is not TaskState.COMPLETED]

    def blocked_or_failed(self) -> list[Task]:
        return [
            t
            for t in self.tasks
            if t.state in (TaskState.FAILED, TaskState.BLOCKED, TaskState.CANCELLED)
        ]

    def progress(self) -> tuple[int, int]:
        return self.completed_count(), self.total

    def percent(self) -> int:
        done, total = self.progress()
        return int(round(100 * done / total)) if total else 0

    # --- mutation ------------------------------------------------------------
    def mark(self, task: Task, state: TaskState, note: str = "") -> None:
        task.mark(state, note)

    def add_recovery_task(
        self,
        title: str,
        detail: str = "",
        acceptance: list[str] | None = None,
        depends_on: list[int] | None = None,
    ) -> Task:
        """Append a task (used when final verification finds a problem)."""
        next_id = max((t.id for t in self.tasks), default=0) + 1
        task = Task(
            id=next_id,
            title=title,
            detail=detail,
            acceptance=acceptance or ["no-errors"],
            depends_on=depends_on or [],
        )
        self.tasks.append(task)
        return task

    # --- rendering -----------------------------------------------------------
    def checklist(self, *, limit: int = 12) -> list[tuple[TaskState, str]]:
        """``(state, title)`` rows for the compact task view."""
        return [(t.state, t.title) for t in self.tasks[: max(1, limit)]]

    # --- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"request": self.request, "tasks": [t.to_dict() for t in self.tasks]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskGraph":
        tasks = [Task.from_dict(t) for t in data.get("tasks") or [] if isinstance(t, dict)]
        return cls(request=str(data.get("request", "")), tasks=tasks)


# --- evidence ----------------------------------------------------------------
_TEST_COMMAND_RE = re.compile(
    r"(?:^|[\s;&|(])"
    r"(?:pytest|py\.test|unittest|tox|nox|ctest|jest|vitest|mocha|rspec|phpunit|"
    r"cargo\s+test|go\s+test|dotnet\s+test|gradle\s+test|mvn\s+test|"
    r"npm\s+(?:run\s+)?test|yarn\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test|"
    r"bun\s+test|deno\s+test|make\s+test|rake\s+test|python3?\s+-m\s+pytest|"
    r"python3?\s+-m\s+unittest)",
    re.IGNORECASE,
)


def is_test_command(command: str) -> bool:
    """Whether ``command`` really is a test run (never a build or a lint)."""
    return bool(_TEST_COMMAND_RE.search(command or ""))


@dataclass
class CommandRecord:
    """One command executed during a task."""

    command: str
    ok: bool
    output: str = ""
    test: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"command": self.command, "ok": self.ok, "test": self.test}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandRecord":
        command = str(data.get("command", ""))
        return cls(
            command=command,
            ok=bool(data.get("ok")),
            output="",
            test=bool(data.get("test")) or is_test_command(command),
        )


@dataclass
class TurnEvidence:
    """What actually happened in one model/tool cycle.

    Everything here is observed, never claimed: files the engine wrote, commands
    the engine ran (with their exit status), tests among those commands, and
    tool errors that were never resolved by a later successful attempt.
    """

    changed_files: list[str] = field(default_factory=list)
    commands: list[CommandRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # What the cycle looked at (read-only tools): the "files inspected" part of
    # the persistent context (§ v7.1.0 session state).
    inspected: list[str] = field(default_factory=list)
    tools: int = 0
    model_calls: int = 0
    # True when the model response (or its output/context limit) ended the
    # cycle without a final answer — the task continues with another call.
    incomplete: bool = False
    # Free-text summary of the last model response (kept for the checkpoint).
    summary: str = ""

    # --- recording -----------------------------------------------------------
    def note_file(self, path: str) -> None:
        path = (path or "").strip()
        if path and path not in self.changed_files:
            self.changed_files.append(path)

    def note_inspected(self, target: str) -> None:
        """Record something a read-only tool really looked at."""
        target = (target or "").strip()
        if target and target not in self.inspected:
            self.inspected.append(target)
            del self.inspected[:-40]

    def note_command(self, command: str, ok: bool, output: str = "") -> None:
        command = (command or "").strip()
        if not command:
            return
        self.commands.append(
            CommandRecord(
                command=command, ok=ok, output=output, test=is_test_command(command)
            )
        )

    def note_error(self, message: str) -> None:
        message = (message or "").strip()
        if message:
            self.errors.append(message.splitlines()[0][:300])

    # --- queries -------------------------------------------------------------
    @property
    def tests(self) -> list[CommandRecord]:
        return [c for c in self.commands if c.test]

    @property
    def tests_passed(self) -> bool:
        """Whether the *latest* test run passed.

        A task that ran the suite, failed, fixed the code and re-ran it has
        passed: what matters is the final state of the project, not that no
        run ever failed (otherwise the fix-and-rerun loop could never verify).
        """
        tests = self.tests
        return bool(tests) and tests[-1].ok

    @property
    def any_success(self) -> bool:
        return bool(self.changed_files) or any(c.ok for c in self.commands)

    def progress_note(self) -> str:
        """A one-line, factual summary of this cycle (never a model claim)."""
        parts: list[str] = []
        if self.changed_files:
            parts.append(f"{len(self.changed_files)} file(s) changed")
        if self.commands:
            ok = sum(1 for c in self.commands if c.ok)
            parts.append(f"{ok}/{len(self.commands)} command(s) ok")
        if self.tests:
            parts.append("tests passed" if self.tests_passed else "tests failed")
        if self.inspected:
            parts.append(f"inspected {len(self.inspected)} item(s)")
        if self.errors:
            parts.append(f"error: {self.errors[-1][:80]}")
        if self.incomplete and not parts:
            parts.append("turn truncated (continuing)")
        return "; ".join(parts)

    def fingerprint(self) -> str:
        """A stable hash of this cycle — used to spot repeated non-progress."""
        parts = [
            ",".join(sorted(self.changed_files)),
            ";".join(f"{c.command}={c.ok}" for c in self.commands),
            ";".join(self.errors[:4]),
        ]
        return "|".join(parts)

    # --- merging -------------------------------------------------------------
    def merge(self, other: "TurnEvidence") -> None:
        """Fold another cycle's evidence into this one (per task).

        A cycle that ran a command successfully and reported no error resolves
        the earlier failures recorded in this accumulator — that is exactly the
        run → fail → fix → re-run → pass loop, and without this rule a task
        could never verify after recovering once.
        """
        for path in other.changed_files:
            self.note_file(path)
        for target in other.inspected:
            self.note_inspected(target)
        self.commands.extend(other.commands)
        for message in other.errors:
            self.note_error(message)
        if not other.errors and any(c.ok for c in other.commands):
            self.errors.clear()
        self.tools += other.tools
        self.model_calls += other.model_calls
        self.incomplete = other.incomplete
        if other.summary:
            self.summary = other.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed_files": list(self.changed_files),
            "commands": [c.to_dict() for c in self.commands],
            "errors": list(self.errors),
            "inspected": list(self.inspected),
            "tools": self.tools,
            "model_calls": self.model_calls,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurnEvidence":
        evidence = cls(
            changed_files=[str(p) for p in data.get("changed_files") or []],
            errors=[str(e) for e in data.get("errors") or []],
            inspected=[str(p) for p in data.get("inspected") or []],
            tools=int(data.get("tools", 0)),
            model_calls=int(data.get("model_calls", 0)),
        )
        evidence.commands = [
            CommandRecord.from_dict(c)
            for c in data.get("commands") or []
            if isinstance(c, dict)
        ]
        return evidence


# --- verification ------------------------------------------------------------
@dataclass(slots=True)
class VerificationOutcome:
    """The result of checking one task's acceptance criteria."""

    ok: bool
    checked: list[str] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)

    @property
    def detail(self) -> str:
        if self.ok:
            count = len(self.checked)
            noun = "criterion" if count == 1 else "criteria"
            return f"verified {count} {noun}"
        return "; ".join(self.unmet[:3]) or "acceptance criteria not met"

    def unmet_prompt(self) -> str:
        """The exact criteria a repair attempt must satisfy."""
        return "\n".join(f"- {item}" for item in self.unmet) or "- (none recorded)"


def command_ok(label: str) -> bool:
    """Whether a recorded command label (``"cmd -> ok"``) succeeded.

    The label format is fixed by :meth:`Task.absorb`, so splitting on the last
    separator is exact; anything unrecognised counts as not-ok (never a silent
    pass).
    """
    return label.rsplit(" -> ", 1)[-1].strip() == "ok"


def evidence_summary(task: Task) -> str:
    """A compact, factual one-line record of what a task really did (v7.1.0).

    Built only from the task's own execution record: the verification result,
    files affected, commands with their outcome, tests, inspected items, tool
    calls and retries. A model's claim of success contributes nothing — a task
    with an empty record says so honestly.
    """
    parts: list[str] = []
    if task.verification:
        parts.append(f"verification: {task.verification}")
    if task.files_affected:
        parts.append(f"files: {len(task.files_affected)}")
    if task.commands:
        ok = sum(1 for label in task.commands if command_ok(label))
        parts.append(f"commands: {ok}/{len(task.commands)} ok")
    if task.tests:
        parts.append(
            "tests: passed" if task.tests[-1].endswith(": passed") else "tests: failed"
        )
    if task.inspected:
        parts.append(f"inspected: {len(task.inspected)}")
    if task.tool_calls:
        parts.append(f"tool calls: {task.tool_calls}")
    if task.attempts:
        parts.append(f"retries: {task.attempts}")
    return "; ".join(parts) or "no tool activity recorded"


def _criterion_check(
    criterion: str, evidence: TurnEvidence, workspace: Path | None
) -> tuple[bool, str]:
    """Check one acceptance criterion; returns (met, description)."""
    text = criterion.strip()
    low = text.lower()

    if low.startswith(f"{_CRITERION_FILE}:"):
        rest = text.split(":", 1)[1].strip()
        path_part, _, contains = rest.partition("|")
        path_part = path_part.strip()
        contains = contains.strip()
        if contains.lower().startswith("contains:"):
            contains = contains.split(":", 1)[1].strip()
        if workspace is None:
            return False, f"no workspace to check {path_part}"
        target = (workspace / path_part).expanduser()
        if not target.is_file():
            return False, f"file {path_part} does not exist"
        if contains:
            try:
                body = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return False, f"file {path_part} could not be read"
            if contains not in body:
                return False, f"file {path_part} does not contain {contains!r}"
            return True, f"file {path_part} contains {contains!r}"
        return True, f"file {path_part} exists"

    if low.startswith(f"{_CRITERION_RUN}:"):
        wanted = text.split(":", 1)[1].strip()
        matches = [
            c for c in evidence.commands if wanted and wanted.lower() in c.command.lower()
        ]
        if not matches:
            return False, f"command {wanted!r} was not run"
        if not any(c.ok for c in matches):
            return False, f"command {wanted!r} did not succeed"
        return True, f"command {wanted!r} succeeded"

    if low.startswith(_CRITERION_TESTS) or "tests pass" in low or "test pass" in low:
        if not evidence.tests:
            return False, "no test run was recorded"
        if not evidence.tests_passed:
            failed = next((c for c in evidence.tests if not c.ok), None)
            return False, f"test run failed: {(failed.command if failed else 'unknown')[:80]}"
        return True, "tests passed"

    if low.startswith(_CRITERION_NO_ERRORS) or low in ("no errors", "no unresolved errors"):
        if evidence.errors:
            return False, f"unresolved error: {evidence.errors[-1][:120]}"
        return True, "no unresolved errors"

    # Free-text criterion: it cannot be machine-checked, so it is only counted
    # when the task produced real, verified work — a documented limitation,
    # never a silent pass.
    if evidence.any_success:
        return True, f"{text} (requires concrete progress: recorded)"
    return False, f"{text} (no concrete evidence of progress yet)"


def verify_task(
    task: Task, evidence: TurnEvidence, workspace: Path | None = None
) -> VerificationOutcome:
    """Check a task's acceptance criteria against observed evidence.

    A task with no machine-checkable criteria still has to prove progress:
    at least one file changed or one command succeeded during the task, with no
    unresolved errors. A failing test run always fails the task.
    """
    criteria = [c for c in task.acceptance if c.strip()]
    checked: list[str] = []
    unmet: list[str] = []

    for criterion in criteria:
        met, description = _criterion_check(criterion, evidence, workspace)
        if met:
            checked.append(description)
        else:
            unmet.append(description)

    if not criteria:
        if evidence.errors:
            unmet.append(f"unresolved error: {evidence.errors[-1][:120]}")
        elif not evidence.any_success:
            unmet.append("no file change or successful command was observed")
        else:
            checked.append("concrete progress observed")

    if evidence.tests and not evidence.tests_passed:
        failed = next(c for c in evidence.tests if not c.ok)
        unmet.append(f"test run failed: {failed.command[:80]}")

    return VerificationOutcome(ok=not unmet, checked=checked, unmet=unmet)


__all__ = [
    "CommandRecord",
    "GLYPHS",
    "PlanItem",
    "TERMINAL_STATES",
    "Task",
    "TaskGraph",
    "TaskState",
    "TurnEvidence",
    "VerificationOutcome",
    "command_ok",
    "evidence_summary",
    "glyph_for",
    "is_test_command",
    "parse_plan",
    "verify_task",
]
