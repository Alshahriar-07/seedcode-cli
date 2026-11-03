"""Internal execution status (v9.3.1, trimmed v9.3.2).

The :class:`Briefing` object is fed exclusively by real backend events — it is
the orchestrator's record of what the selected provider and model are doing
right now:

* ``status``         — the real session phase (Analyzing, Executing, Testing…)
* ``task``           — the task the task graph is actually working on
* ``activity``       — the tool/operation in flight
* ``last_completed`` — the last operation that really finished
* ``next_action``    — the next runnable task, when the graph knows one
* ``retry``          — the real retry/reconnect information (never invented)
* ``error``          — the current blocker, when there is one

v9.3.2: this is **backend state only** — none of it is rendered into the main
terminal any more. The former full-width "LIVE AI BRIEFING" dashboard (title,
model/provider identity, status and field labels) is gone; the user-facing
bottom strip is the compact reasoning display only (``Thinking...`` + the
model's own exposed reasoning, both dim). The fields stay available for
debugging, logs and ``/review``-style reporting.

This module is pure data — no terminal I/O, no prompt_toolkit import.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Briefing"]


@dataclass
class Briefing:
    """What the selected model is doing, from real execution events only."""

    #: True while a turn/session is genuinely running. Drives the idle line.
    active: bool = False
    status: str = ""
    task: str = ""
    activity: str = ""
    last_completed: str = ""
    next_action: str = ""
    retry: str = ""
    error: str = ""

    def clear(self) -> None:
        """Return to the idle state (no request in flight)."""
        self.active = False
        self.status = ""
        self.task = ""
        self.activity = ""
        self.last_completed = ""
        self.next_action = ""
        self.retry = ""
        self.error = ""
