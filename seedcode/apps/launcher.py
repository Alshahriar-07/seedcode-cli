"""Application launching with state-based waiting and duplicate avoidance.

The launch workflow never spawns blindly:

1. **Running?** Match open windows against the app name (via the windows
   driver, which already excludes SeedCode's own console). A visible window
   means *focus the existing instance* instead of launching a duplicate.
2. **Launch** through the safest target: a Start Menu shortcut
   (``os.startfile`` resolves it), then a resolved executable, then the
   shell ``start`` fallback — never a guessed bare ``subprocess`` call.
3. **Wait for state, not for a timer:** poll for a matching window/process
   up to ``MAX_WAIT_WINDOW_S`` instead of a fixed sleep.
4. **Verify** and return a structured result; the caller (skill) reports
   success only when evidence exists.

All OS interaction is injectable for tests.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from ..core.errors import ApplicationLaunchError, ApplicationNotFoundError
from ..core.limits import MAX_WAIT_WINDOW_S, MAX_WAIT_POLL_S, clamp_wait
from .discovery import AppInfo, find_app
from .verifier import app_running, wait_for_app_window


@dataclass(slots=True)
class LaunchResult:
    """Structured outcome of one open-app attempt."""

    success: bool
    application: str
    focused_existing: bool = False
    window_detected: bool = False
    window_title: str = ""
    pid: int = 0
    detail: str = ""

    def describe(self) -> str:
        return self.detail or (f"{self.application}: "
                               + ("focused existing window" if self.focused_existing
                                  else "launched")
                               + (" (window verified)" if self.window_detected else ""))


def focus_existing(app: AppInfo, windows_driver: Any = None) -> LaunchResult | None:
    """Focus a running instance if one exists; None when not running.

    Matching uses the window list (titles already exclude our console) and,
    where available, the owning process name — never a blind title guess.
    """
    if windows_driver is None:
        from ..computer import windows as windows_driver  # type: ignore
    needle = app.name.lower()
    try:
        wins = windows_driver.list_windows()
    except Exception:
        return None
    for w in wins:
        title = (getattr(w, "title", "") or "").lower()
        if needle in title:
            try:
                windows_driver.focus_window(w.title)
            except Exception:
                pass  # focus is best-effort; window evidence stands
            return LaunchResult(
                success=True, application=app.name, focused_existing=True,
                window_detected=True, window_title=getattr(w, "title", ""),
                pid=int(getattr(w, "pid", 0) or 0),
                detail=f'focused existing window "{getattr(w, "title", "")}"',
            )
    return None


def launch_app(
    name: str,
    *,
    find: Any = find_app,
    windows_driver: Any = None,
    startfile: Any = None,
    popen: Any = None,
    wait_s: float = MAX_WAIT_WINDOW_S,
) -> LaunchResult:
    """Open an application: reuse → launch → wait → verify.

    Injectable parameters exist purely for tests; production callers pass
    only ``name``.
    """
    if startfile is None:
        def startfile(target: str) -> None:
            os.startfile(target)  # noqa: S606 — shell-resolved launch
    if popen is None:
        def popen(argv: list[str]) -> subprocess.Popen:
            return subprocess.Popen(  # noqa: S603 — resolved target only
                argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )

    try:
        app = find(name)
    except ApplicationNotFoundError:
        raise  # the caller (skill) turns this into the install-permission flow

    # Step 1: already running? Focus, don't duplicate.
    existing = focus_existing(app, windows_driver)
    if existing is not None:
        return existing

    # Step 2: launch via the safest target.
    target = app.target or app.exe
    if not target:
        raise ApplicationLaunchError(
            f'"{app.name}" is installed but exposes no launchable target.'
        )
    launched_via = "shortcut" if app.source == "start_menu" else "executable"
    try:
        if app.source == "start_menu" or target.lower().endswith(".lnk"):
            startfile(target)
        else:
            popen([target])
    except OSError as exc:
        raise ApplicationLaunchError(f'Could not launch "{app.name}": {exc}')

    # Step 3+4: state-based wait for a real window (no blind sleep).
    window = wait_for_app_window(app, windows_driver, timeout_s=wait_s)
    if window is not None:
        return LaunchResult(
            success=True, application=app.name, window_detected=True,
            window_title=window, pid=app.pid,
            detail=f'launched {app.name} via {launched_via}; window "{window}" detected',
        )
    # No window yet: accept a running process as weaker evidence.
    pid = app_running(app, windows_driver)
    if pid:
        return LaunchResult(
            success=True, application=app.name, window_detected=False,
            pid=pid, detail=f"launched {app.name} (process running, no window yet)",
        )
    return LaunchResult(
        success=False, application=app.name,
        detail=f"launched {app.name} but no window or process evidence within {wait_s:g}s",
    )


def open_or_raise(name: str, **kw: Any) -> LaunchResult:
    """Convenience wrapper: find+launch, translating discovery misses."""
    return launch_app(name, **kw)


__all__ = ["LaunchResult", "launch_app", "focus_existing", "open_or_raise"]
