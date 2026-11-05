"""Session-scoped execution memory: ``.seedcode/sessions/<session_id>/``.

One user request is one session, and a session owns its own durable state::

    .seedcode/sessions/session_<id>/
        todo.json        the complete plan and per-task status
        progress.json    where execution stands (current task, counts, reason)
        execution.json   the ordered log of real events (tool calls, results,
                         verification evidence, repairs)

This is what makes an interrupted run resumable *honestly*: on restart the
session can see which task was mid-flight, what it had already changed and
which failures it had seen — instead of assuming an interrupted task finished.

Only observed operations are written. No transcript, no file contents, no
credentials. Every file is written atomically, and loading tolerates a corrupt
document (the caller falls back to the workspace).
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..utils.atomic import read_json, write_json_atomic

__all__ = [
    "MAX_EXECUTION_EVENTS",
    "SessionStore",
    "list_sessions",
    "new_session_id",
]

#: The execution log is capped: it is a working record, not an archive.
MAX_EXECUTION_EVENTS = 400
_SESSIONS_DIRNAME = "sessions"


def new_session_id() -> str:
    """A sortable, collision-resistant session id."""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _safe(value: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", str(value or ""))[:limit]
    return cleaned or "session"


class SessionStore:
    """Read/write access to one session's own directory."""

    def __init__(
        self,
        workspace: Path,
        session_id: str | None = None,
        *,
        root: Path | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.root = Path(root) if root is not None else self.workspace / ".seedcode"
        self.session_id = session_id or new_session_id()
        self.directory = (
            self.root / _SESSIONS_DIRNAME / f"session_{_safe(self.session_id)}"
        )

    # --- paths ---------------------------------------------------------------
    @property
    def todo_path(self) -> Path:
        return self.directory / "todo.json"

    @property
    def progress_path(self) -> Path:
        return self.directory / "progress.json"

    @property
    def execution_path(self) -> Path:
        return self.directory / "execution.json"

    def ensure(self) -> bool:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False

    # --- todo ----------------------------------------------------------------
    def save_todo(self, graph: Any) -> bool:
        """Persist the plan exactly as the task graph holds it."""
        payload = graph.to_dict() if hasattr(graph, "to_dict") else dict(graph or {})
        payload = dict(payload)
        payload.setdefault("session_id", self.session_id)
        payload["updated_at"] = time.time()
        return write_json_atomic(self.todo_path, payload)

    def load_todo(self) -> dict[str, Any] | None:
        data = read_json(self.todo_path)
        return data if isinstance(data, dict) else None

    # --- progress ------------------------------------------------------------
    def save_progress(
        self,
        state: Any = None,
        *,
        status: str = "",
        current_task: dict[str, Any] | None = None,
        model_calls: int = 0,
        tool_calls: int = 0,
        reason: str = "",
        next_action: str = "",
    ) -> bool:
        """Persist where execution stands (the resumable checkpoint summary)."""
        to_dict = getattr(state, "to_dict", None)
        state_data = to_dict() if callable(to_dict) else {}
        completed = list(state_data.get("completed") or [])
        remaining = list(state_data.get("remaining") or [])
        total = len(completed) + len(remaining)
        percent = int(round(100 * len(completed) / total)) if total else 0
        payload = {
            "version": 1,
            "session_id": self.session_id,
            "workspace": str(self.workspace),
            "status": status,
            "updated_at": time.time(),
            "current_task": current_task,
            "completed": completed,
            "remaining": remaining,
            "progress": {
                "completed": len(completed),
                "total": total,
                "percent": percent,
            },
            "model_calls": int(model_calls),
            "tool_calls": int(tool_calls),
            "reason": reason,
            "next_action": next_action,
        }
        return write_json_atomic(self.progress_path, payload)

    def load_progress(self) -> dict[str, Any] | None:
        data = read_json(self.progress_path)
        return data if isinstance(data, dict) else None

    # --- execution log -------------------------------------------------------
    def append_execution(self, event: Any) -> bool:
        """Append one real event to the ordered execution log."""
        entry = event.to_dict() if hasattr(event, "to_dict") else dict(event or {})
        data = self.load_execution()
        events = list(data.get("events") or [])
        events.append(entry)
        del events[:-MAX_EXECUTION_EVENTS]
        payload = {
            "version": 1,
            "session_id": self.session_id,
            "updated_at": time.time(),
            "events": events,
        }
        return write_json_atomic(self.execution_path, payload)

    def load_execution(self) -> dict[str, Any]:
        data = read_json(self.execution_path)
        if not isinstance(data, dict):
            return {"version": 1, "session_id": self.session_id, "events": []}
        if not isinstance(data.get("events"), list):
            data["events"] = []
        return data

    # --- whole-state convenience --------------------------------------------
    def sync(
        self,
        graph: Any = None,
        state: Any = None,
        *,
        status: str = "",
        current_task: dict[str, Any] | None = None,
        model_calls: int = 0,
        tool_calls: int = 0,
        reason: str = "",
        next_action: str = "",
    ) -> None:
        """Write the plan and the progress snapshot (best-effort, never fatal)."""
        if not self.ensure():
            return
        if graph is not None:
            self.save_todo(graph)
        self.save_progress(
            state,
            status=status,
            current_task=current_task,
            model_calls=model_calls,
            tool_calls=tool_calls,
            reason=reason,
            next_action=next_action,
        )

    # --- retention -----------------------------------------------------------
    def archive(self, *, keep: int = 20) -> int:
        """Move older *completed* sessions into ``sessions/archive``.

        Resumable sessions are never archived: an unfinished run must stay
        where ``/resume`` looks for it.
        """
        base = self.root / _SESSIONS_DIRNAME
        if not base.is_dir():
            return 0
        folders = sorted(
            (p for p in base.iterdir() if p.is_dir() and p.name.startswith("session_")),
            key=lambda p: p.name,
            reverse=True,
        )
        archive_dir = base / "archive"
        moved = 0
        for folder in folders[max(0, int(keep)):]:
            progress = read_json(folder / "progress.json") or {}
            if not isinstance(progress, dict):
                continue
            if str(progress.get("status")) not in ("completed", "cancelled"):
                continue
            try:
                archive_dir.mkdir(parents=True, exist_ok=True)
                folder.rename(archive_dir / folder.name)
                moved += 1
            except OSError:
                continue
        return moved


def list_sessions(workspace: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    """Recent sessions with their real progress, newest first."""
    base = Path(workspace) / ".seedcode" / _SESSIONS_DIRNAME
    if not base.is_dir():
        return []
    try:
        folders = sorted(
            (p for p in base.iterdir() if p.is_dir() and p.name.startswith("session_")),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for folder in folders[: max(1, int(limit))]:
        progress = read_json(folder / "progress.json")
        progress = progress if isinstance(progress, dict) else {}
        out.append(
            {
                "session_id": str(progress.get("session_id") or folder.name),
                "directory": str(folder),
                "status": str(progress.get("status") or "unknown"),
                "progress": progress.get("progress") or {},
                "current_task": progress.get("current_task"),
                "updated_at": progress.get("updated_at") or 0,
            }
        )
    return out
