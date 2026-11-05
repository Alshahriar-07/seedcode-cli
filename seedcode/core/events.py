"""Structured live-activity events emitted by the backend (v9.2.0).

The agent loop reports what it is doing through two observers: the legacy
``(kind, detail)`` callback and the structured ``on_step`` payload. This module
turns each real operation into one :class:`ActivityEvent` with a stable id, a
timestamp, a task id, a type, a human message, the affected path, an operation
status, real hunk line numbers and the unified diff (when a file changed).

Events are only ever built from an operation that actually happened: a tool
that started, a tool that finished with its real output/diff, a terminal line
that was printed, or the turn itself starting/completing. No event is generated
from a UI timer or a model's description of its work.
"""

from __future__ import annotations

import itertools
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..tools.diffs import diff_details
from ..utils.text import safe_text
from .tasks import is_test_command

__all__ = [
    "ActivityEvent",
    "NormalizedEvent",
    "build_event",
    "event_type_for",
    "next_event_id",
    "normalize_event",
    "protocol_type",
]

_SEQ = itertools.count(1)


def next_event_id() -> str:
    """A process-unique, monotonically increasing event id."""
    return f"evt-{next(_SEQ)}"


#: Tools that only inspect the project.
_SCAN_TOOLS = frozenset({"list_dir", "find_files", "search_text", "project_index"})


def event_type_for(
    phase: str,
    name: str = "",
    ok: bool | None = None,
    args: dict[str, Any] | None = None,
    diff: str = "",
) -> str:
    """Resolve the event type for one real phase of the agent loop."""
    tool = (name or "").lower()
    args = args or {}
    command = str(args.get("command") or "")

    if phase == "say":
        return "thinking"
    if phase == "task_started":
        return "task_started"
    if phase == "task_completed":
        return "task_completed"
    if phase == "task_failed":
        return "task_failed"
    if phase == "terminal_output":
        return "terminal_output"
    if phase == "tool_start":
        if tool == "run_command":
            return "test_started" if is_test_command(command) else "terminal_started"
        if tool == "web_search":
            return "internet_search"
        if tool == "web_fetch":
            return "internet_result"
        return "tool_started"
    if phase != "tool_done":
        return "success"
    if ok is False:
        return "error"

    details = diff_details(diff)
    if tool == "run_command":
        return "test_completed" if is_test_command(command) else "terminal_completed"
    if tool == "web_search":
        return "internet_search"
    if tool == "web_fetch":
        return "internet_result"
    if tool == "write_file":
        return "file_created" if details["created"] else "file_updated"
    if tool in ("append_file", "edit_file", "insert_in_file"):
        return "file_updated"
    if tool == "delete_file":
        return "file_deleted"
    if tool == "create_directory":
        return "folder_created"
    if tool == "delete_directory":
        return "folder_deleted"
    if tool == "read_file":
        return "file_read"
    if tool in _SCAN_TOOLS:
        return "project_scan"
    if tool == "git":
        return "terminal_completed"
    return "success"


def _path_from_args(args: dict[str, Any] | None) -> str:
    for key in ("path", "source", "destination", "file", "url"):
        value = (args or {}).get(key)
        if value:
            return safe_text(str(value))[:500]
    return ""


def _one_line(text: str) -> str:
    return " ".join(safe_text(text or "").split())


def _exit_code(output: str, ok: bool | None) -> int:
    """The real exit code reported by the terminal tool, else 0/1.

    The tool writes ``✗ FAILED (exit code N)`` into its own output, so the
    number is read from the operation, never invented by the UI.
    """
    match = re.search(r"exit code (\d+)", output or "")
    if match:
        return int(match.group(1))
    return 0 if ok else 1


def _message_for(
    kind: str,
    *,
    name: str,
    ok: bool | None,
    path: str,
    text: str,
    output: str,
    args: dict[str, Any] | None,
    details: dict,
    phase: str = "",
) -> str:
    """A short human-readable line describing the real operation."""
    added = int(details.get("added") or 0)
    removed = int(details.get("removed") or 0)
    command = str((args or {}).get("command") or "")
    query = str((args or {}).get("query") or "")
    label = name or "tool"

    if kind == "task_started":
        return f"Starting task: {_one_line(text)[:180]}" if text else "Starting task"
    if kind == "task_completed":
        return "Task completed"
    if kind == "task_failed":
        return f"Task failed: {_one_line(text)[:180]}" if text else "Task failed"
    if kind == "thinking":
        return _one_line(text)[:220] or "Thinking..."
    if kind == "terminal_output":
        return _one_line(output)[:220]
    if kind == "file_read":
        return f"Read {path}"
    if kind == "file_created":
        return f"Created {path} (+{added} lines)"
    if kind == "file_updated":
        return f"Updated {path} (+{added}/-{removed} lines)"
    if kind == "file_deleted":
        return f"Deleted {path} (-{removed} lines)"
    if kind == "folder_created":
        return f"Created folder {path}"
    if kind == "folder_deleted":
        return f"Deleted folder {path}"
    if kind == "project_scan":
        if query:
            return f"Searched the project for {query!r}"
        return f"Analyzed project structure ({path})" if path else "Analyzed project structure"
    if kind == "terminal_started":
        if is_test_command(command):
            return f"Running tests: {command}"
        return f"Running command: {command}" if command else "Running a command"
    if kind == "test_started":
        return f"Running tests: {command}"
    if kind == "terminal_completed":
        # The real exit code, straight from the tool's own output — never a
        # guess: "✓ Command completed · Exit code: 0 · python -m pytest".
        code = _exit_code(output, ok)
        if ok:
            return f"Command completed · Exit code: {code}"
        return f"Command failed · Exit code: {code}"
    if kind == "test_completed":
        if ok:
            return f"Tests passed · Exit code: {_exit_code(output, True)}"
        return f"Tests failed: {_one_line(output)[:160]}"
    if kind == "internet_search":
        started = phase == "tool_start"
        if query:
            verb = "Searching" if started else "Searched"
            return f"{verb} online for {query!r}"
        return "Searching online documentation" if started else "Searched online documentation"
    if kind == "internet_result":
        verb = "Reading" if phase == "tool_start" else "Read"
        return f"{verb} online source: {path}" if path else f"{verb} an online source"
    if kind == "error":
        detail = _one_line(output)
        return f"{label} failed: {detail[:180]}" if detail else f"{label} failed"
    if kind == "tool_started":
        return f"{label} started" + (f" {path}" if path else "")
    if kind == "success":
        return f"{label} completed" + (f" {path}" if path else "")
    return f"{label} completed" + (f" {path}" if path else "")


@dataclass(slots=True)
class ActivityEvent:
    """One real backend operation, ready for the UI/event stream."""

    event_id: str
    task_id: int
    timestamp: float
    type: str
    message: str
    path: str = ""
    status: str = ""
    diff: str = ""
    line: int = 0
    line_end: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    hunks: list[dict[str, int]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation (the same fields, no dataclass)."""
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "type": self.type,
            "message": self.message,
            "path": self.path,
            "status": self.status,
            "diff": self.diff,
            "line": self.line,
            "line_end": self.line_end,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "hunks": [dict(h) for h in self.hunks],
            "metadata": dict(self.metadata),
        }


def build_event(
    *,
    phase: str,
    task_id: int = 0,
    name: str = "",
    args: dict[str, Any] | None = None,
    ok: bool | None = None,
    output: str = "",
    text: str = "",
    diff: str = "",
    path: str = "",
    **extra: Any,
) -> ActivityEvent:
    """Build the event for one real phase (tool start/done, terminal, task)."""
    clean_diff = safe_text(diff or "")
    details = diff_details(clean_diff)
    kind = event_type_for(phase, name, ok, args, clean_diff)
    resolved_path = safe_text(path) if path else _path_from_args(args)
    status = {
        "tool_start": "started",
        "tool_done": "ok" if ok else "failed",
        "task_started": "started",
        "task_completed": "completed",
        "task_failed": "failed",
        "terminal_output": "running",
        "say": "ok",
    }.get(phase, "ok")
    metadata: dict[str, Any] = {
        "phase": phase,
        "tool": name or "",
        "output_chars": len(output or ""),
        "created": bool(details.get("created")),
        "deleted": bool(details.get("deleted")),
        "file_diff": bool(clean_diff),
    }
    for key, value in (args or {}).items():
        if key == "content":
            metadata["content_chars"] = len(str(value))
        elif isinstance(value, (str, int, float, bool)) and len(str(value)) <= 200:
            metadata[key] = value
    metadata.update({k: v for k, v in extra.items() if v is not None})
    return ActivityEvent(
        event_id=next_event_id(),
        task_id=int(task_id or 0),
        timestamp=time.time(),
        type=kind,
        message=_message_for(
            kind,
            name=name,
            ok=ok,
            path=resolved_path,
            text=text,
            output=output,
            args=args,
            details=details,
            phase=phase,
        ),
        path=resolved_path,
        status=status,
        diff=clean_diff,
        line=int(details.get("line") or 0),
        line_end=int(details.get("line_end") or 0),
        lines_added=int(details.get("added") or 0),
        lines_removed=int(details.get("removed") or 0),
        hunks=list(details.get("hunks") or []),
        metadata=metadata,
    )


# --- normalized protocol (v10.2.0) ------------------------------------------
#: Legacy session kinds -> the normalized event protocol. The UI and the
#: session's execution.json consume these names; an unknown kind degrades to
#: ``session.updated`` rather than inventing an event category.
_PROTOCOL_TYPES: dict[str, str] = {
    "session_started": "session.started",
    "session_running": "session.updated",
    "session_completed": "session.completed",
    "session_paused": "execution.paused",
    "session_cancelled": "execution.cancelled",
    "session_failed": "session.failed",
    "plan": "analysis.started",
    "plan_ready": "plan.created",
    "task_start": "todo.updated",
    "task_done": "todo.completed",
    "task_failed": "todo.updated",
    "task_blocked": "todo.updated",
    "task_skipped": "todo.updated",
    "continue": "todo.updated",
    "verify": "verification.started",
    "final_verify": "verification.started",
    "final_ok": "verification.completed",
    "recovery": "repair.started",
    "complete": "session.completed",
    "retry": "model.waiting",
    "reconnect": "model.waiting",
    "reconnected": "model.connected",
    # v10.4.0: the agent's own execution protocol. These names are stable for
    # embedders; the frontend renders them and never drives the loop.
    "agent_started": "agent.started",
    "agent_completed": "agent.completed",
    "agent_failed": "agent.failed",
    "agent_cancelled": "agent.cancelled",
    "iteration": "agent.iteration",
    "thinking": "agent.reasoning",
    "waiting_model": "model.waiting",
    "model_response": "model.response.completed",
    "stream_completed": "model.response.completed",
    "stream_failed": "model.response.failed",
    "network_lost": "model.response.failed",
    "error": "session.failed",
    "tool_start": "tool.started",
    "tool_done": "tool.completed",
    "say": "model.text.delta",
    "terminal_output": "tool.updated",
}

_TASK_ID_RE = re.compile(r"\bTask\s+(\d+)", re.IGNORECASE)


@dataclass(slots=True)
class NormalizedEvent:
    """One session event in the stable wire protocol.

    Every event carries the session id, its own stable id, a real timestamp,
    the task it belongs to (0 when it is session-scoped) and a structured
    payload. Events are only constructed from an operation that happened.
    """

    event_id: str
    session_id: str
    type: str
    timestamp: float
    task_id: int
    message: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "type": self.type,
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "message": self.message,
            "payload": dict(self.payload),
        }


def protocol_type(kind: str) -> str:
    """The normalized event type for a legacy or already-normalized kind."""
    key = (kind or "").strip()
    if "." in key:
        return key
    return _PROTOCOL_TYPES.get(key, "session.updated")


def normalize_event(
    kind: str,
    detail: str = "",
    *,
    session_id: str = "",
    task_id: int = 0,
    payload: dict[str, Any] | None = None,
) -> NormalizedEvent:
    """Normalize one real session event (never a simulated one).

    ``kind`` is the session's own event kind; the mapping gives it a stable
    protocol type. The task id is taken from the explicit value, otherwise from
    the real ``Task <id>`` the message names.
    """
    message = _one_line(detail)[:400]
    resolved_task = int(task_id or 0)
    if not resolved_task:
        match = _TASK_ID_RE.search(detail or "")
        if match:
            resolved_task = int(match.group(1))
    data = dict(payload or {})
    data.setdefault("source", "session")
    data.setdefault("kind", (kind or "").strip())
    if message:
        data.setdefault("detail", message)
    return NormalizedEvent(
        event_id=next_event_id(),
        session_id=str(session_id or ""),
        type=protocol_type(kind),
        timestamp=time.time(),
        task_id=resolved_task,
        message=message,
        payload=data,
    )
