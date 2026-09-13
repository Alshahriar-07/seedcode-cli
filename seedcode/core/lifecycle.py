"""Agent lifecycle: a single, explicit state machine for the whole app.

The auto-exit bug was architectural, not cosmetic: nothing in the codebase
distinguished "a task finished" from "the application should terminate", so
termination could be triggered from deep inside a tool run (a window-close
that hit our own terminal, an unhandled exception escaping a turn, a cleanup
path that escalated). This module makes that structurally impossible:

* The REPL is always the owner of process lifetime. A turn is a *function
  call* into the engine; whatever the turn does — succeed, fail, crash, get
  cancelled — control returns to the prompt. Nothing below the REPL may end
  the process.
* Shutdown is a *decision*, made in exactly one place: :func:`request_exit`,
  called only when the user explicitly asks to quit (the /exit command, the
  menu's Exit item, or Ctrl+D). No tool, task outcome, or error path can
  reach it.
* The state machine records where the app is (IDLE → PLANNING → EXECUTING →
  VERIFYING → RESPONDING → IDLE) and enforces that the only legal transition
  out of the task cycle is back to IDLE. Entering SHUTDOWN is validated:
  it is legal *only* from IDLE, and only when an exit was explicitly
  requested. A stray attempt (a bug somewhere deep calling shutdown after a
  task) raises :class:`LifecycleError` loudly instead of silently dying.

Keep this module dependency-free: everything in the app should be able to
import it.
"""

from __future__ import annotations

import enum
import threading
from typing import Callable


class LifecycleError(RuntimeError):
    """An illegal lifecycle transition was attempted (a real bug somewhere)."""


@enum.unique
class Phase(str, enum.Enum):
    """Where the application currently is in the task cycle."""

    IDLE = "idle"                # at the prompt, waiting for the user
    PLANNING = "planning"        # a turn started; the model is thinking
    EXECUTING = "executing"      # tools / desktop actions are running
    VERIFYING = "verifying"      # outcomes are being checked
    RESPONDING = "responding"    # the final answer is being produced
    SHUTDOWN = "shutdown"        # the app is exiting (terminal state)


# Legal transitions. The task cycle is a strict forward chain that must end
# back at IDLE; IDLE may also go straight to SHUTDOWN (explicit exit).
_ALLOWED: dict[Phase, frozenset[Phase]] = {
    Phase.IDLE: frozenset({Phase.PLANNING, Phase.SHUTDOWN}),
    Phase.PLANNING: frozenset({Phase.EXECUTING, Phase.IDLE}),
    Phase.EXECUTING: frozenset({Phase.VERIFYING, Phase.IDLE}),
    Phase.VERIFYING: frozenset({Phase.RESPONDING, Phase.IDLE}),
    Phase.RESPONDING: frozenset({Phase.IDLE}),  # NEVER SHUTDOWN from here
    # SHUTDOWN is terminal: no transitions out (validated implicitly because
    # nothing lists SHUTDOWN as a source).
    Phase.SHUTDOWN: frozenset(),
}

# Phases a user turn passes through, in order.
_TASK_PATH = (Phase.PLANNING, Phase.EXECUTING, Phase.VERIFYING, Phase.RESPONDING)


class Lifecycle:
    """The app-wide lifecycle state machine (one instance per process).

    The REPL advances it around each user turn; the exit decision is the only
    path to SHUTDOWN. Thread-safe because desktop work can run on helper
    threads.
    """

    def __init__(self) -> None:
        self._phase = Phase.IDLE
        # Set only by :meth:`request_exit` — the explicit user decision.
        self._exit_requested = False
        self._lock = threading.RLock()
        self._on_shutdown: list[Callable[[], None]] = []

    # --- introspection -------------------------------------------------------
    @property
    def phase(self) -> Phase:
        with self._lock:
            return self._phase

    @property
    def exit_requested(self) -> bool:
        """Whether the user has explicitly asked to quit."""
        with self._lock:
            return self._exit_requested

    def is_running(self) -> bool:
        """Whether the app should keep running (the REPL's loop condition)."""
        with self._lock:
            return not self._exit_requested and self._phase is not Phase.SHUTDOWN

    # --- turn transitions ----------------------------------------------------
    def begin_turn(self) -> None:
        """IDLE → PLANNING. A user turn starts (raises unless at IDLE)."""
        with self._lock:
            self._transition(Phase.PLANNING)

    def to_executing(self) -> None:
        with self._lock:
            self._transition(Phase.EXECUTING)

    def to_verifying(self) -> None:
        with self._lock:
            self._transition(Phase.VERIFYING)

    def to_responding(self) -> None:
        with self._lock:
            self._transition(Phase.RESPONDING)

    def end_turn(self) -> None:
        """Whatever phase the turn reached → IDLE. Always call in ``finally``.

        Every path out of a turn — success, tool failure, provider error,
        cancellation, even a crash inside the engine — funnels here, which is
        precisely the guarantee the auto-exit bug violated.
        """
        with self._lock:
            if self._phase is Phase.IDLE:
                return  # a nested/sub-turn already closed out
            self._transition(Phase.IDLE)

    # --- the one exit path ---------------------------------------------------
    def request_exit(self, reason: str = "") -> None:
        """Record the user's explicit decision to quit.

        Only interactive, unambiguous user actions may call this: the /exit
        command, the menu's Exit item, or Ctrl+D at the prompt. A task's
        completion — however it ends — must never reach this method.
        """
        with self._lock:
            self._exit_requested = True
            if reason:
                import logging

                logging.getLogger("seedcode.lifecycle").info(
                    "exit requested: %s", reason
                )

    def shutdown(self) -> None:
        """Enter SHUTDOWN and run registered teardown hooks.

        Validates the transition: legal only from IDLE with an explicit exit
        request. A cleanup path or tool that tries to shut the app down after
        (or during) a task fails loudly instead of killing the process.
        """
        with self._lock:
            if self._phase is Phase.SHUTDOWN:
                return
            if self._phase is not Phase.IDLE:
                raise LifecycleError(
                    f"shutdown attempted from {self._phase.value} — a task is "
                    "still running. Shutdown is only legal from IDLE after an "
                    "explicit exit request."
                )
            if not self._exit_requested:
                raise LifecycleError(
                    "shutdown attempted without an explicit exit request — "
                    "normal task completion must never terminate the app."
                )
            self._transition(Phase.SHUTDOWN)
            hooks = list(self._on_shutdown)
        for hook in hooks:
            try:
                hook()
            except Exception:
                pass  # teardown is best-effort by contract

    def on_shutdown(self, hook: Callable[[], None]) -> None:
        """Register a best-effort teardown hook (flush logs, save state...)."""
        with self._lock:
            self._on_shutdown.append(hook)

    def task_span(self) -> "_TaskSpan":
        """Context manager for one full user turn: begin → … → end, always.

        Usage in the REPL::

            with lifecycle().task_span():
                run_the_whole_turn()

        Whatever the turn does — succeed, fail, raise, get cancelled — the
        ``finally`` returns the machine to IDLE so the REPL prompts again.
        """
        return _TaskSpan(self)

    # --- internals -----------------------------------------------------------
    def _transition(self, target: Phase) -> None:
        if target not in _ALLOWED[self._phase]:
            raise LifecycleError(
                f"Illegal lifecycle transition {self._phase.value} -> "
                f"{target.value}. Legal: "
                f"{', '.join(p.value for p in _ALLOWED[self._phase])}."
            )
        self._phase = target

    def reset_for_tests(self) -> None:
        """Back to IDLE with no exit request (test isolation only)."""
        with self._lock:
            self._phase = Phase.IDLE
            self._exit_requested = False
            self._on_shutdown.clear()


# The process-wide lifecycle. The REPL owns it; everything else may read it.
_lifecycle = Lifecycle()


def lifecycle() -> Lifecycle:
    """The process-wide :class:`Lifecycle` instance."""
    return _lifecycle


class _TaskSpan:
    """One full user turn on a :class:`Lifecycle` (see :meth:`Lifecycle.task_span`)."""

    def __init__(self, lc: Lifecycle) -> None:
        self._lc = lc

    def __enter__(self) -> "_TaskSpan":
        self._lc.begin_turn()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Swallow nothing: the exception (if any) propagates to the REPL's
        # own handler; the lifecycle just guarantees the return to IDLE.
        self._lc.end_turn()
        return False


def task_span() -> "_TaskSpan":
    """One full user turn on the process-wide lifecycle."""
    return _lifecycle.task_span()
