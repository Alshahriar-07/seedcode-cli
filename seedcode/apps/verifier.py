"""Application launch verification: evidence, not assumptions.

``launch`` alone proves nothing on Windows — the shell accepts the request
and the app may crash a second later. This module checks for *evidence*
that the application actually came up:

* a window whose title matches the app (strong evidence), or
* a running process whose name matches (weaker evidence), via the window
  list's owning pids (psutil when present, best-effort).

``wait_for_app_window`` polls — it never sleeps blindly — and returns the
matched window title, or None after the bounded timeout.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.limits import MAX_WAIT_WINDOW_S, MAX_WAIT_POLL_S, clamp_wait
from .discovery import AppInfo


def _process_names() -> set[int]:
    """Pids of running processes whose name plausibly matches apps (best-effort)."""
    pids: set[int] = set()
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["pid"]):
            try:
                pids.add(int(proc.info["pid"]))
            except Exception:
                continue
    except Exception:
        pass
    return pids


def _window_fragments(app: AppInfo) -> list[str]:
    """Title fragments that identify this app's windows."""
    name = (app.name or "").lower().strip()
    fragments = [name] if name else []
    exe = (app.exe or "")
    if exe:
        stem = exe.replace("\\", "/").rsplit("/", 1)[-1]
        stem = stem[:-4] if stem.lower().endswith(".exe") else stem
        if stem:
            fragments.append(stem.lower())
    return fragments


def find_app_window(app: AppInfo, windows_driver: Any = None) -> str | None:
    """The title of a window belonging to ``app``, or None."""
    if windows_driver is None:
        from ..computer import windows as windows_driver  # type: ignore
    try:
        wins = windows_driver.list_windows()
    except Exception:
        return None
    fragments = _window_fragments(app)
    # Prefer process-id equality when the window carries one.
    for w in wins:
        if app.pid and int(getattr(w, "pid", 0) or 0) == app.pid:
            return getattr(w, "title", "")
    for w in wins:
        title = (getattr(w, "title", "") or "").lower()
        if any(f and f in title for f in fragments):
            return getattr(w, "title", "")
    return None


def app_running(app: AppInfo, windows_driver: Any = None) -> int:
    """The app's running pid (from windows/processes), or 0."""
    if windows_driver is None:
        from ..computer import windows as windows_driver  # type: ignore
    try:
        wins = windows_driver.list_windows()
    except Exception:
        wins = []
    fragments = _window_fragments(app)
    for w in wins:
        title = (getattr(w, "title", "") or "").lower()
        if any(f and f in title for f in fragments):
            pid = int(getattr(w, "pid", 0) or 0)
            if pid:
                return pid
    # No window: consult live processes by exe stem.
    live = _process_names()
    if not live:
        return 0
    try:
        import psutil  # type: ignore

        exe_stem = (app.exe or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info.get("name") or "").lower()
            if exe_stem and name == exe_stem:
                return int(proc.info["pid"])
    except Exception:
        pass
    return 0


def wait_for_app_window(
    app: AppInfo, windows_driver: Any = None, timeout_s: float = MAX_WAIT_WINDOW_S
) -> str | None:
    """Poll for the app's window until it appears or the timeout elapses."""
    deadline = time.monotonic() + clamp_wait(timeout_s, MAX_WAIT_WINDOW_S)
    while True:
        title = find_app_window(app, windows_driver)
        if title:
            return title
        if time.monotonic() >= deadline:
            return None
        time.sleep(MAX_WAIT_POLL_S)


__all__ = ["find_app_window", "app_running", "wait_for_app_window"]
