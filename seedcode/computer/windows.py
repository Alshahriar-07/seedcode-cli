"""Window management driver: list, focus, open, and close applications.

Window enumeration and focus use ``pygetwindow`` (Win32 under the hood).
Opening applications goes through ``os.startfile``/``start`` semantics so
anything resolvable by the shell (path, registered app, document) works.
Closing is graceful first (WM_CLOSE) with a ``taskkill`` fallback by name.

**Self-protection:** every lookup skips SeedCode's own console window (see
:mod:`.selfguard`). Title matching here is substring-based, so without the
guard a request like "close the browser" could resolve to — and terminate —
the very terminal SeedCode is running in. Controlling our own window is
deliberately impossible; closing *SeedCode* is the REPL's /exit decision,
never a window operation.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from . import selfguard


@dataclass(slots=True)
class WindowInfo:
    """One top-level window as reported to the model."""

    title: str
    left: int
    top: int
    width: int
    height: int
    active: bool
    minimized: bool
    # Identity metadata (0 when the platform doesn't expose it): the owning
    # process lets the app controller match windows to launches, and the raw
    # handle lets semantic actions drive windows precisely. Our own console
    # is reported with pid=0/hwnd=0 so no matching logic can ever pick it.
    pid: int = 0
    hwnd: int = 0

    def describe(self) -> str:
        state = "active" if self.active else ("minimized" if self.minimized else "open")
        return (
            f'"{self.title}" [{state}] at ({self.left}, {self.top}) '
            f"size {self.width}x{self.height}"
        )


def _gw():
    import pygetwindow

    return pygetwindow


def _window_pid_hwnd(win: Any) -> tuple[int, int]:
    """Owning process id + handle of a pygetwindow window (0, 0 when unknown).

    ``pygetwindow`` exposes the raw handle as ``_hWnd``; the pid comes from
    Win32. Failures degrade to (0, 0) — matching code must treat that as
    "unknown", never as a match.
    """
    hwnd = getattr(win, "_hWnd", 0) or 0
    pid = 0
    if sys.platform == "win32" and hwnd:
        try:
            import ctypes

            pid_val = ctypes.wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_val))
            pid = int(pid_val.value)
        except Exception:
            pid = 0
    return pid, int(hwnd)


def list_windows() -> list[WindowInfo]:
    """All titled top-level windows, active one first."""
    gw = _gw()
    active = gw.getActiveWindow()
    active_handle = getattr(active, "_hWnd", None)
    windows = []
    for win in gw.getAllWindows():
        if not (win.title or "").strip():
            continue
        if selfguard.is_own_window(win):
            continue  # never report the terminal running SeedCode
        pid, hwnd = _window_pid_hwnd(win)
        windows.append(
            WindowInfo(
                title=win.title,
                left=int(win.left),
                top=int(win.top),
                width=int(win.width),
                height=int(win.height),
                active=getattr(win, "_hWnd", None) == active_handle,
                minimized=bool(win.isMinimized),
                pid=pid,
                hwnd=hwnd,
            )
        )
    windows.sort(key=lambda w: not w.active)
    return windows


def active_window() -> WindowInfo | None:
    """The focused window, or None when nothing is focused."""
    win = _gw().getActiveWindow()
    if win is None or not (win.title or "").strip():
        return None
    if selfguard.is_own_window(win):
        return None  # our console is invisible to desktop logic
    pid, hwnd = _window_pid_hwnd(win)
    return WindowInfo(
        title=win.title,
        left=int(win.left),
        top=int(win.top),
        width=int(win.width),
        height=int(win.height),
        active=True,
        minimized=bool(win.isMinimized),
        pid=pid,
        hwnd=hwnd,
    )


def _find(title_substring: str):
    """First window whose title contains ``title_substring`` (case-insensitive).

    SeedCode's own console is invisible to this lookup: focusing, closing, or
    otherwise acting on the terminal hosting the agent is always a mistake.
    """
    needle = title_substring.strip().lower()
    if not needle:
        raise ValueError("Window title (or part of it) is required.")
    for win in _gw().getAllWindows():
        if selfguard.is_own_window(win):
            continue  # never target the terminal running SeedCode
        if needle in (win.title or "").lower():
            return win
    raise ValueError(f"No window found matching '{title_substring}'.")


def focus_window(title_substring: str) -> WindowInfo | None:
    """Bring a window to the foreground; returns the new active window."""
    win = _find(title_substring)
    if win.isMinimized:
        win.restore()
    win.activate()
    return active_window()


def open_app(target: str) -> str:
    """Launch an application or open a document via the shell.

    ``target`` is anything the Windows shell can resolve: an exe name on
    PATH, a full path, or a registered app (e.g. "notepad", "calc").
    ShellExecute via ``os.startfile`` first (documents, App Paths), then a
    detached ``start`` as fallback — never waiting on the launched process,
    which would hang until the app exits.
    """
    import os

    target = target.strip()
    if not target:
        raise ValueError("Application name or path is required.")
    try:
        os.startfile(target)  # noqa: S606 - deliberate shell-open semantics
        return f"Launched '{target}'."
    except OSError:
        pass
    # Fallback: 'start' resolves PATH executables and shell aliases. The
    # process is detached (no inherited pipes) so this returns immediately.
    try:
        subprocess.Popen(
            f'start "" "{target}"',
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise RuntimeError(f"Could not open '{target}': {exc}")
    return f"Launched '{target}'."


def close_window(title_substring: str, force: bool = False) -> str:
    """Close a window gracefully (WM_CLOSE); ``force`` kills the process.

    Refuses outright when the target *is* our own console: closing SeedCode
    is an explicit /exit decision made by the user in the REPL, never a
    side effect of a desktop task. The ``taskkill`` fallback is additionally
    title-guarded so a force-kill can never match our terminal by prefix.
    """
    if selfguard.is_own_title(title_substring):
        raise ValueError(
            "Refusing to close SeedCode's own terminal. Use /exit in the "
            "REPL to quit SeedCode."
        )
    win = _find(title_substring)
    title = win.title
    win.close()
    if force:
        # Best-effort process kill for apps that ignore WM_CLOSE. The window
        # filter is matched as a prefix, so pin it away from our own title.
        if selfguard.is_own_title(title):
            return f"Closed '{title}'."  # graceful close succeeded; skip the kill
        subprocess.run(
            f'taskkill /FI "WINDOWTITLE eq {title}*" /F',
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    return f"Closed '{title}'."
