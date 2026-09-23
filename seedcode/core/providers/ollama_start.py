"""Ollama server auto-start (v7.2.5).

When the user selects Ollama, Seed Code should not make them open a terminal
and run ``ollama serve`` themselves. This module:

1. checks whether the server is already answering on the configured host;
2. checks **again** immediately before launching, so a server that came up in
   the meantime is never duplicated;
3. launches ``ollama serve`` detached, cross-platform (``os.name`` drives a
   POSIX session/Windows detached process — nothing Windows-only leaks into
   the provider layer);
4. polls for readiness with a bounded timeout instead of blocking forever;
5. reports a useful, actionable message on every outcome.

It never raises: the caller gets ``(ok, message)`` and can decide what to do.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Callable

import httpx

from .. import http as pooled_http

#: How long to wait for a freshly started server to answer.
DEFAULT_STARTUP_TIMEOUT_S = 20.0
#: Delay between readiness probes.
DEFAULT_POLL_INTERVAL_S = 0.5
#: Per-probe timeout (short: a local server answers instantly when up).
_PROBE_TIMEOUT_S = 2.0


def server_up(host: str, *, timeout: float = _PROBE_TIMEOUT_S) -> bool:
    """True when an Ollama server answers ``GET /api/tags`` on ``host``."""
    try:
        response = pooled_http.get(f"{host.rstrip('/')}/api/tags", timeout=timeout)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def ensure_running(
    config,
    *,
    on_status: Callable[[str], None] | None = None,
    timeout: float = DEFAULT_STARTUP_TIMEOUT_S,
    poll_interval: float = DEFAULT_POLL_INTERVAL_S,
    which: Callable[[str], str | None] = shutil.which,
    popen: Callable[..., object] = subprocess.Popen,
    detect: Callable[[str], bool] = server_up,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> tuple[bool, str]:
    """Ensure the Ollama server is running; return ``(ok, message)``.

    ``detect``, ``popen``, ``sleep`` and ``now`` are injectable so the logic is
    testable without a real server or process.
    """
    host = (getattr(config, "ollama_host", "") or "http://localhost:11434").rstrip("/")

    def status(message: str) -> None:
        if on_status is not None:
            try:
                on_status(message)
            except Exception:
                pass  # a UI callback must never break startup

    if detect(host):
        return True, f"Ollama server already running at {host}."

    status("Starting Ollama…")
    executable = which("ollama") or which("ollama.exe")
    if not executable:
        return (
            False,
            "Ollama is not installed or not on PATH. Install it from "
            "https://ollama.com, then select Ollama again.",
        )

    # Re-check right before launching: another process may have started the
    # server between the first check and now (never launch a duplicate).
    if detect(host):
        return True, f"Ollama server already running at {host}."

    try:
        popen([executable, "serve"], **_spawn_options())
    except OSError as exc:
        return False, f"Could not start 'ollama serve': {exc}"

    status("Waiting for Ollama…")
    deadline = now() + max(0.0, float(timeout))
    while now() < deadline:
        sleep(max(0.0, float(poll_interval)))
        if detect(host):
            return True, f"Ollama server started at {host}."
    return (
        False,
        f"Ollama did not become ready within {timeout:g}s. It may still be "
        "starting — try again, or run 'ollama serve' manually.",
    )


def _spawn_options() -> dict:
    """Detached-process options for the current platform.

    Windows gets a new process group with no console window and no inherited
    handles; POSIX gets a new session so the server outlives this process.
    """
    options: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":  # pragma: no cover - platform specific
        options["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        options["start_new_session"] = True
    return options


__all__ = [
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_STARTUP_TIMEOUT_S",
    "ensure_running",
    "server_up",
]
