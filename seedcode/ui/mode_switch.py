"""Explicit, non-blocking mode transitions (v8.2.5 TUI stability).

Switching modes used to be a bare ``threading.Thread`` started per keystroke,
with no owner, no cancellation and no staleness check: ``Chat → Agent → Chat →
Agent`` left several initializers racing over the shared configuration and the
process-wide ``.seedcode`` state, and the last one to finish won the header —
whichever mode the user had actually asked for.

This module makes a transition a first-class object instead:

* **one owner** — the UI controller holds the switcher, and the switcher owns
  every worker it starts; nothing else may start one;
* **single flight per target** — asking for the mode that is already being
  prepared is a no-op, not a second worker;
* **cancellation** — starting a new transition cancels the previous one, and
  the work receives a ``cancel()`` probe it is expected to poll;
* **staleness** — a superseded worker reports *nothing*: its result, its
  errors and its messages are discarded, so a slow old scan can never
  overwrite the state a newer switch established;
* **no blocking, ever** — :meth:`ModeSwitcher.run` returns immediately. The
  caller (a UI event callback) is never made to wait for the work;
* **no silent failures** — an exception inside the work becomes a reported
  error, not a lost thread, and never a frozen interface.

It deliberately does **not** introduce asyncio: Seed Code's UI is
prompt_toolkit on the main thread with worker threads underneath, so a worker
plus a cancellation event is the mechanism that already fits.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

__all__ = ["ModeSwitcher", "SwitchError"]

#: The cancellation probe handed to the work of a transition.
Cancel = Callable[[], bool]

#: What a transition runs: it receives the cancellation probe and returns
#: whatever it likes (the return value is passed to ``on_finished``).
Work = Callable[[Cancel], Any]

#: How long a new transition lets a superseded worker stop before starting its
#: own work. Stale workers poll the probe they were given, so this is normally
#: a few milliseconds; the bound only exists so a worker stuck in an
#: uninterruptible call (a subprocess probe, say) cannot stall a newer switch.
_STALE_HANDOVER_S = 5.0


class SwitchError(RuntimeError):
    """A transition's work raised (kept so the caller can report it verbatim)."""


class ModeSwitcher:
    """Owns every background mode transition for one UI session.

    ``on_started`` says what the UI should show the instant a transition is
    requested (for Agent Mode: the mode it is entering, and that it is
    initializing). ``on_finished`` / ``on_failed`` are called **from the
    worker** when the *current* transition completes; a superseded one calls
    neither. All three must be cheap and thread-safe — the TUI's implementations
    only touch :class:`~seedcode.ui.state.AppState` and request a repaint.
    """

    def __init__(
        self,
        *,
        on_started: Callable[[int, str], None] | None = None,
        on_finished: Callable[[int, str, Any], None] | None = None,
        on_failed: Callable[[int, str, str], None] | None = None,
        name: str = "seedcode-mode",
    ) -> None:
        self._on_started = on_started
        self._on_finished = on_finished
        self._on_failed = on_failed
        self._name = name

        self._lock = threading.RLock()
        self._generation = 0
        self._key = ""                       # the target of the current transition
        self._cancels: dict[int, threading.Event] = {}
        self._workers: dict[int, threading.Thread] = {}
        #: Generations that have actually begun their work (see
        #: :meth:`working_workers`): the strict single-initialization guarantee.
        self._working: set[int] = set()

    # --- introspection ------------------------------------------------------
    @property
    def generation(self) -> int:
        """The newest requested transition (0 before the first one)."""
        with self._lock:
            return self._generation

    @property
    def key(self) -> str:
        """The target of the newest transition (``"agent"`` / ``"chat"``)."""
        with self._lock:
            return self._key

    @property
    def active(self) -> bool:
        """Whether a transition is still running (the newest one, or a stale)."""
        with self._lock:
            return bool(self._workers)

    def live_workers(self) -> int:
        """How many transition threads are still alive (a settling count).

        A superseded worker is counted until it has actually stopped, which is
        why this settles to ``0`` rather than staying at ``1``: nothing is
        leaked, it is just on its way out.
        """
        with self._lock:
            return sum(1 for worker in self._workers.values() if worker.is_alive())

    def working_workers(self) -> int:
        """How many transitions are currently *doing* their work.

        This is the guarantee behind "repeated switching must not create
        multiple Agent workers": a new transition waits for the superseded one
        to stop before it starts (see :meth:`_await_older`), so this never
        exceeds one even when several switches are requested in a row.
        """
        with self._lock:
            return len(self._working)

    # --- starting / cancelling ---------------------------------------------
    def run(self, key: str, work: Work) -> int:
        """Start ``work`` on one worker; return its generation. Never blocks.

        ``key`` names the transition's target. Asking again for the target that
        is already being prepared returns the running generation and starts
        nothing — repeated ``/agent on`` cannot fork a second initializer.
        """
        with self._lock:
            if key and key == self._key and self._any_alive():
                return self._generation
            self._generation += 1
            generation = self._generation
            self._key = key
            # Every older transition is stale from this moment: it must stop,
            # and (see ``_run``) it will not report anything.
            for cancel in self._cancels.values():
                cancel.set()
            cancel = threading.Event()
            self._cancels[generation] = cancel
            # Daemon-ness is a safety net for interpreter shutdown, not the
            # lifecycle mechanism: ownership, cancellation and the staleness
            # check above are what actually stop a superseded transition.
            worker = threading.Thread(
                target=self._run,
                args=(generation, key, work, cancel),
                name=f"{self._name}-{generation}",
                daemon=True,
            )
            self._workers[generation] = worker
            started = self._on_started
        # Report outside the lock: a UI callback must not be able to re-enter.
        if started is not None:
            try:
                started(generation, key)
            except Exception:
                pass  # a UI hint must never break the transition
        worker.start()
        return generation

    def cancel(self, *, wait: float = 0.0) -> None:
        """Cancel the current transition (and any stale one).

        ``wait`` blocks up to that many seconds for the workers to finish —
        used on shutdown and in tests, never from a UI callback.
        """
        with self._lock:
            cancels = list(self._cancels.values())
            workers = [w for w in self._workers.values() if w.is_alive()]
            self._key = ""
        for cancel in cancels:
            cancel.set()
        if wait > 0:
            deadline = _monotonic() + wait
            for worker in workers:
                # ``Thread.join`` refuses a non-started thread; a worker we
                # started always has been, but stay defensive.
                try:
                    worker.join(max(0.0, deadline - _monotonic()))
                except RuntimeError:
                    continue

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the workers to settle; True when none is still alive."""
        with self._lock:
            workers = list(self._workers.values())
        deadline = None if timeout is None else _monotonic() + timeout
        for worker in workers:
            remaining = None if deadline is None else max(0.0, deadline - _monotonic())
            try:
                worker.join(remaining)
            except RuntimeError:
                continue
        return self.live_workers() == 0

    # --- the worker ---------------------------------------------------------
    def _any_alive(self) -> bool:
        return any(worker.is_alive() for worker in self._workers.values())

    def _await_older(self, generation: int) -> None:
        """Let superseded workers stop before this one starts its work.

        Strict turn-taking, on the worker thread: only one Agent Mode
        initialization is ever *running*, which is what keeps
        ``Chat → Agent → Chat → Agent`` from walking the project twice. The
        interface is untouched by the wait — it never happens on the UI thread.
        """
        deadline = _monotonic() + _STALE_HANDOVER_S
        while True:
            with self._lock:
                older = [
                    worker
                    for gen, worker in self._workers.items()
                    if gen < generation and worker.is_alive()
                ]
            if not older or _monotonic() >= deadline:
                return
            for worker in older:
                try:
                    worker.join(0.05)
                except RuntimeError:
                    pass  # not started (defensive): nothing to wait for

    def _run(
        self,
        generation: int,
        key: str,
        work: Work,
        cancel: threading.Event,
    ) -> None:
        error = ""
        result: Any = None
        try:
            self._await_older(generation)
            with self._lock:
                self._working.add(generation)
            result = work(cancel.is_set)
        except KeyboardInterrupt:
            error = "cancelled"
        except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
            error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._working.discard(generation)
                self._workers.pop(generation, None)
                self._cancels.pop(generation, None)
                stale = generation != self._generation
            if stale:
                return  # a superseded transition reports nothing at all
            if error:
                handler = self._on_failed
                if handler is not None:
                    try:
                        handler(generation, key, error)
                    except Exception:
                        pass
            else:
                handler = self._on_finished
                if handler is not None:
                    try:
                        handler(generation, key, result)
                    except Exception:
                        pass


def _monotonic() -> float:
    import time

    return time.monotonic()
