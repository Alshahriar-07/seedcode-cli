"""Durable per-project task state: ``.seedcode/todo.json`` (v10.4.0).

The agent's plan must survive a turn, a crash and a resume. This module owns
the small, human-readable file at the project root; it records the plan the
model actually committed to, never a completion it merely claimed.

The file is a working record, not proof: :mod:`seedcode.core.agent` still
verifies completion against real tool evidence. Writes are atomic, and a
corrupt document loads as ``None`` rather than breaking the turn.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils.atomic import read_json, write_json_atomic

__all__ = [
    "STEP_STATUSES",
    "TASK_STATUSES",
    "TaskState",
    "TaskStateStore",
    "normalize_steps",
]

TASK_STATUSES = frozenset({"pending", "in_progress", "completed", "failed", "cancelled"})
STEP_STATUSES = frozenset({"pending", "in_progress", "completed", "failed", "skipped"})


def _status(value: Any, allowed: frozenset[str], default: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    return text if text in allowed else default


def normalize_steps(raw: Any) -> list[dict[str, Any]]:
    """Coerce model-supplied steps into the durable step shape.

    Accepts a JSON string, a list of strings, or a list of objects. Ids are
    renumbered from 1 so the file stays sequential even when a smaller model
    forgets them.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            # A plain multi-line plan is still a valid plan: one step per line.
            entries = [line.strip(" -*\t") for line in text.splitlines() if line.strip()]
            raw = entries
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    steps: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str):
            description = item.strip()
            status = "pending"
            detail = ""
        elif isinstance(item, dict):
            description = str(
                item.get("description") or item.get("title") or item.get("step") or ""
            ).strip()
            status = _status(item.get("status"), STEP_STATUSES, "pending")
            detail = str(item.get("detail") or item.get("result") or "").strip()
        else:
            continue
        if not description:
            continue
        steps.append(
            {
                "id": len(steps) + 1,
                "description": description[:240],
                "status": status,
                "detail": detail[:400],
            }
        )
    return steps


@dataclass(slots=True)
class TaskState:
    """The current plan and where execution stands."""

    task: str = ""
    status: str = "pending"
    steps: list[dict[str, Any]] = field(default_factory=list)
    current_step: int = 0
    last_result: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "status": _status(self.status, TASK_STATUSES, "pending"),
            "steps": [dict(step) for step in self.steps],
            "current_step": int(self.current_step or 0),
            "last_result": self.last_result,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TaskState | None":
        if not isinstance(data, dict):
            return None
        steps = normalize_steps(data.get("steps") or [])
        # Preserve recorded statuses/details: normalize_steps keeps them, but a
        # legacy document may omit ids.
        return cls(
            task=str(data.get("task") or "")[:500],
            status=_status(data.get("status"), TASK_STATUSES, "pending"),
            steps=steps,
            current_step=max(0, int(data.get("current_step") or 0)),
            last_result=str(data.get("last_result") or "")[:800],
            updated_at=float(data.get("updated_at") or time.time()),
        )

    def advance(self) -> int:
        """Mark the current step in progress/finished and move forward."""
        if not self.steps:
            return 0
        for index, step in enumerate(self.steps):
            if step.get("status") not in ("completed", "skipped"):
                step["status"] = "in_progress"
                self.current_step = int(step.get("id") or index + 1)
                if index > 0:
                    self.steps[index - 1]["status"] = "completed"
                return self.current_step
        self.status = "completed"
        self.current_step = len(self.steps)
        return self.current_step

    def progress(self) -> tuple[int, int]:
        done = sum(1 for step in self.steps if step.get("status") in ("completed", "skipped"))
        return done, len(self.steps)

    def render(self, *, max_chars: int = 900) -> str:
        """Compact prompt section, honestly labelled as a working record."""
        if not self.task and not self.steps:
            return ""
        done, total = self.progress()
        lines = [
            "CURRENT TASK STATE (.seedcode/todo.json — a working record, not proof):",
            f"Task: {self.task or '(unnamed)'}",
            f"Status: {self.status}  ·  Progress: {done}/{total}",
        ]
        for step in self.steps:
            mark = {
                "completed": "[x]",
                "in_progress": "[>]",
                "failed": "[!]",
                "skipped": "[-]",
            }.get(str(step.get("status")), "[ ]")
            lines.append(f"{mark} {step.get('id')}. {step.get('description')}")
            if step.get("detail"):
                lines.append(f"      {step['detail']}")
        if self.last_result:
            lines.append(f"Last result: {self.last_result}")
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


class TaskStateStore:
    """Read/write the project's durable task file."""

    def __init__(self, workspace: str | Path, *, path: str | Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.path = (
            Path(path)
            if path is not None
            else self.workspace / ".seedcode" / "todo.json"
        )

    def load(self) -> TaskState | None:
        data = read_json(self.path)
        return TaskState.from_dict(data)

    def save(self, state: TaskState) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        state.updated_at = time.time()
        return write_json_atomic(self.path, state.to_dict())

    def clear(self) -> bool:
        try:
            self.path.unlink()
            return True
        except OSError:
            return False

    def update_from_tool(self, args: dict[str, Any]) -> TaskState:
        """Apply one ``update_task`` tool call to the durable state."""
        state = self.load() or TaskState()
        task = str(args.get("task") or "").strip()
        if task:
            state.task = task[:500]
        if "steps" in args and args.get("steps") not in (None, ""):
            state.steps = normalize_steps(args.get("steps"))
            state.current_step = 0
        if "status" in args:
            state.status = _status(args.get("status"), TASK_STATUSES, state.status)
        if "current_step" in args:
            try:
                current = int(args.get("current_step") or 0)
            except (TypeError, ValueError):
                current = 0
            state.current_step = max(0, min(current, len(state.steps)))
            for step in state.steps:
                if int(step.get("id") or 0) == state.current_step:
                    step["status"] = "in_progress"
        if "last_result" in args:
            state.last_result = str(args.get("last_result") or "")[:800]
        return state

    def render(self) -> str:
        state = self.load()
        return state.render() if state is not None else ""
