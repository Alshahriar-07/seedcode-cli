"""Self-guard: SeedCode must never act on its own terminal window or process.

This module exists to close the loop-hole behind the auto-exit bug: window
and process operations that match by *title substring* (``close_window``,
``taskkill /FI "WINDOWTITLE eq ..."``, focus-then-hotkey workflows) could
match SeedCode's own console window and terminate the host terminal — which
kills SeedCode with it. Sending a task like "close the browser" must never be
able to escalate into "close the terminal running SeedCode".

Every driver that can close, kill, or type into a window consults this
module first. Identification is positive — a window is only treated as ours
when we can *prove* it is (window handle equality, owning process in our
process tree, or an exact title match with the hosting console) — so the
guard can never block legitimate automation of unrelated windows:

* fail-open for unknown windows (they are simply not ours), and
* fail-closed for actions that would touch a proven-own target.

Pure stdlib (ctypes) on Windows; a safe no-op everywhere else, so importing
this module is free on any platform and in tests.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Any

_windows_ok = sys.platform == "win32"
if _windows_ok:
    import ctypes.wintypes  # noqa: F401 — DWORD for GetWindowThreadProcessId

# Cache the process tree for the life of the process: our ancestry cannot
# change while we run, and walking it is not free.
_ancestors: "set[int] | None" = None


# --- process identification ---------------------------------------------------
def own_pid() -> int:
    """The current SeedCode process id."""
    return os.getpid()


def ancestor_pids(max_depth: int = 10) -> set[int]:
    """SeedCode's process id plus every ancestor's (shell, terminal host...).

    Walking upward matters on modern Windows: Windows Terminal hosts the
    shell in its own process, so the console window SeedCode runs inside may
    be owned by an *ancestor* (wt.exe / WindowsTerminal.exe), not by us.
    Killing that window would kill our terminal — exactly the auto-exit bug.

    Best-effort: psutil is optional; without it only our own pid is returned.
    """
    global _ancestors
    if _ancestors is not None:
        return _ancestors
    pids = {own_pid()}
    if not _windows_ok:
        _ancestors = pids
        return _ancestors
    try:
        import psutil  # type: ignore

        proc = psutil.Process(own_pid())
        for _ in range(max_depth):
            parent = proc.parent()
            if parent is None or parent.pid in (0, pids):
                break
            pids.add(parent.pid)
            proc = parent
    except Exception:
        # psutil missing or a process vanished — self alone is still correct.
        pass
    _ancestors = pids
    return _ancestors


def is_own_process(pid: Any) -> bool:
    """Whether ``pid`` belongs to SeedCode or the terminal hosting it."""
    try:
        return int(pid) in ancestor_pids()
    except (TypeError, ValueError):
        return False


# --- console window identification ---------------------------------------------
def console_hwnd() -> int:
    """Window handle of the console hosting us (0 when detached/non-Windows)."""
    if not _windows_ok:
        return 0
    try:
        return int(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return 0


def _window_pid(hwnd: int) -> int:
    """Process id owning ``hwnd`` (0 when unavailable)."""
    if not _windows_ok or not hwnd:
        return 0
    try:
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def _window_title(hwnd: int) -> str:
    """Title text of ``hwnd`` (empty when unavailable)."""
    if not _windows_ok or not hwnd:
        return ""
    try:
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:
        return ""


def console_title() -> str:
    """The hosting console's current title ('' when detached/non-Windows)."""
    hwnd = console_hwnd()
    return _window_title(hwnd) if hwnd else ""


def foreground_hwnd() -> int:
    """Window handle of the foreground window (0 when unavailable)."""
    if not _windows_ok:
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return 0


def is_own_hwnd(hwnd: Any) -> bool:
    """Whether a window handle is SeedCode's own console (or its host).

    Positive identification only: handle equality with the hosting console,
    or a window whose owning process is in our process tree (catches Windows
    Terminal, whose console API returns no classic console handle). Unknown
    handles are *not* ours by definition — fail-open.
    """
    if not _windows_ok or not hwnd:
        return False
    try:
        hwnd_int = int(hwnd)
    except (TypeError, ValueError):
        return False
    if hwnd_int and hwnd_int == console_hwnd():
        return True
    return is_own_process(_window_pid(hwnd_int))


def foreground_is_own() -> bool:
    """Whether the currently focused window is SeedCode's own terminal.

    Keystrokes synthesized while *our* window is focused were meant for some
    other window whose focus attempt failed — delivering them here types into
    the user's prompt or triggers our own key bindings. Callers refuse.
    """
    hwnd = foreground_hwnd()
    if not hwnd:
        return False
    return is_own_hwnd(hwnd)


# --- pygetwindow-object helpers --------------------------------------------------
def is_own_window(win: Any) -> bool:
    """Whether a pygetwindow-style window object is SeedCode's own terminal.

    Checks the object's ``_hWnd`` when present (the reliable path), falling
    back to an *exact* title match with the hosting console. A substring is
    deliberately NOT enough: "Command Prompt" also matches every other
    console on the machine, and the user may legitimately want those closed.
    """
    hwnd = getattr(win, "_hWnd", None)
    if hwnd is not None and is_own_hwnd(hwnd):
        return True
    title = (getattr(win, "title", "") or "").strip()
    own = console_title().strip()
    return bool(title) and bool(own) and title.lower() == own.lower()


def is_own_title(title: str) -> bool:
    """Whether a window *title* is exactly our hosting console's title.

    Used as a belt-and-braces check before ``taskkill`` window-title filters:
    the filter matches by prefix, so a kill command whose filter equals our
    console title would target our own terminal.
    """
    own = console_title().strip()
    candidate = (title or "").strip()
    return bool(candidate) and bool(own) and candidate.lower() == own.lower()
