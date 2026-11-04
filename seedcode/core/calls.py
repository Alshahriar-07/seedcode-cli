"""Tool-call state management (OpenCode session-tool parity).

Every tool call the engine executes gets a call record: a UNIQUE id, its
name and arguments, its lifecycle status, and real timestamps (start,
finish, duration). Two guarantees come out of this:

* **Duplicate-call prevention** — a provider call id that was already
  answered in this turn is never executed again; the recorded result is
  replayed to the model instead (providers occasionally resend a call id
  on a retry; executing it twice would corrupt file-state reasoning).
* **Honest reporting** — the UI and the session evidence see the actual
  duration and terminal status of every call, not a guess.

Statuses: ``pending`` → ``running`` → ``completed`` | ``failed`` |
``blocked`` (denied by permissions/hooks/profile) | ``deduped`` (duplicate
call id replayed). One tracker instance covers ONE agent turn; the engine
resets it at turn start.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..utils.text import safe_text


STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"
STATUS_DEDUPED = "deduped"


@dataclass(slots=True)
class ToolCallState:
    """The execution state of ONE tool call."""

    call_id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_PENDING
    started_at: float | None = None
    finished_at: float | None = None
    ok: bool | None = None
    output: str = ""
    error: str = ""

    @property
    def duration_ms(self) -> int | None:
        """Wall-clock execution duration, when the call has finished timing."""
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0, int((end - self.started_at) * 1000))

    def to_dict(self) -> dict[str, Any]:
        """A UI/evidence-friendly structural summary (never fabricates)."""
        return {
            "call_id": self.call_id,
            "tool": self.tool,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "ok": self.ok,
            "error": self.error,
        }


class CallTracker:
    """Records the state of every tool call in one agent turn."""

    def __init__(self) -> None:
        self._seq = 0
        self.records: dict[str, ToolCallState] = {}

    def start(self, tool: str, args: dict[str, Any], call_id: str = "") -> ToolCallState:
        """Open a record for this call (unique id guaranteed)."""
        self._seq += 1
        record_id = (call_id or "").strip() or f"agent-{self._seq}"
        if record_id in self.records:  # provider collides with a local id
            self._seq += 1
            record_id = f"{record_id}#{self._seq}"
        state = ToolCallState(call_id=record_id, tool=tool, args=dict(args or {}))
        self.records[record_id] = state
        return state

    def duplicate_of(self, call_id: str) -> ToolCallState | None:
        """An ALREADY-FINISHED record with this provider call id, if any."""
        if not call_id:
            return None
        seen = self.records.get(call_id)
        if seen is not None and seen.status in (
            STATUS_COMPLETED,
            STATUS_FAILED,
            STATUS_BLOCKED,
            STATUS_DEDUPED,
        ):
            return seen
        return None

    def begin(self, state: ToolCallState) -> None:
        """Mark a call as executing now."""
        if state.status != STATUS_DEDUPED:
            state.status = STATUS_RUNNING
        state.started_at = time.monotonic()

    def finish(
        self,
        state: ToolCallState,
        ok: bool,
        output: str,
        error: str = "",
        *,
        blocked: bool = False,
        deduped: bool = False,
    ) -> None:
        """Close a record with its real outcome; output text is normalized."""
        state.finished_at = time.monotonic()
        state.ok = ok
        state.output = safe_text((output or "")[:2000])
        state.error = safe_text(error or "")
        if deduped:
            state.status = STATUS_DEDUPED
        elif blocked:
            state.status = STATUS_BLOCKED
        elif ok:
            state.status = STATUS_COMPLETED
        else:
            state.status = STATUS_FAILED

    def reset(self) -> None:
        """Clear all records (one tracker instance = one agent turn)."""
        self._seq = 0
        self.records.clear()

    def summary(self) -> dict[str, Any]:
        """Counts by status for the turn report / tests."""
        counts: dict[str, int] = {}
        for state in self.records.values():
            counts[state.status] = counts.get(state.status, 0) + 1
        return {"calls": len(self.records), "statuses": counts}
