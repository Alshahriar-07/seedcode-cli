"""Central retry/step limits for the operator stack.

One module owns every bound so a confused loop can never spin forever and
the numbers are tunable in one place:

* ``MAX_ACTION_RETRIES``     — one action re-tried after failure
* ``MAX_RECOVERY_ATTEMPTS``  — recovery-strategy applications per failure
* ``MAX_STALE_REQUERY``      — stale-element re-query cycles per action
* ``MAX_PLAN_STEPS``         — planner steps per goal (the agent loop has its
  own unrelated ``MAX_STEPS`` for tool calls; this bounds plan expansion)
* ``MAX_TASK_DURATION_S``    — wall-clock ceiling for one orchestrated task
* ``MAX_WAIT_FOR_*``         — state-based wait ceilings (no blind sleeps)

All values are plain ints/floats; a config layer may clamp them downward but
the defaults are the safety floor.
"""

from __future__ import annotations

MAX_ACTION_RETRIES = 2
MAX_RECOVERY_ATTEMPTS = 2
MAX_STALE_REQUERY = 2
MAX_PLAN_STEPS = 12
MAX_TASK_DURATION_S = 300.0

# State-based waits (used instead of blind sleeps everywhere).
MAX_WAIT_WINDOW_S = 10.0     # a window/app to appear
MAX_WAIT_ELEMENT_S = 8.0     # a UI/DOM element to appear
MAX_WAIT_NAVIGATION_S = 15.0  # a page navigation to settle
MAX_WAIT_POLL_S = 0.25       # poll cadence for state waits


def clamp_wait(seconds: float, ceiling: float) -> float:
    """Clamp a wait to [0, ceiling]; negative becomes 0."""
    return max(0.0, min(float(seconds), ceiling))
