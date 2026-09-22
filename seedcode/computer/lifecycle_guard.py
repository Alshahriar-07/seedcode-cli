"""Application-lifecycle guard (v7.1.0): opened apps stay open.

The reported bug was: *Seed Code opens an application, does the work, then the
application closes.* The cause was structural — nothing distinguished
"Seed Code opened this and is tidying up" from "the user asked for this app to
close". Any cleanup-ish path (a teardown hook, a workflow that resets state, a
blind ``Ctrl+W``) could therefore terminate a user-facing application.

This module draws the line where it belongs:

* **Launch ledger.** :func:`mark_launched` records every application/window
  SeedCode itself opened this session (``open_app``, browser navigation). The
  ledger is what makes "the app is open because we opened it" a known fact.
* **Explicit intent required.** :func:`guard_close` refuses to close anything
  unless the code is running inside :func:`explicit_close` — i.e. the user (or
  the model acting on a direct user request) actually asked for that close.
  A cleanup path that forgets to say so now fails loudly instead of silently
  closing the user's browser.
* **Cleanup is internal only.** :func:`release_internal` is the teardown hook:
  it drops cached connections/drivers and never touches a window or a process.

Explicit requests still work exactly as before: ``close_app``/``app_close`` and
``browser_close`` wrap themselves in :func:`explicit_close`, and forced kills
require the same explicit intent. Legitimate internal cleanup (mouse/keyboard
release, DevTools sockets, cached engines) is untouched by any of this.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from ..utils.logger import get_logger

_log = get_logger("computer.lifecycle")

__all__ = [
    "ImplicitCloseRefused",
    "LaunchedApp",
    "closing_explicitly",
    "explicit_close",
    "guard_close",
    "launched_apps",
    "mark_launched",
    "release_internal",
    "reset",
]


class ImplicitCloseRefused(RuntimeError):
    """A close was attempted implicitly (not by an explicit user request)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(slots=True)
class LaunchedApp:
    """An application SeedCode opened during this session."""

    target: str
    title: str
    opened_at: float

    def matches(self, needle: str) -> bool:
        """Whether a window title/target refers to this launch."""
        needle = (needle or "").strip().lower()
        if not needle:
            return False
        if self.title and needle in self.title.lower():
            return True
        if self.target and (needle in self.target.lower() or self.target.lower() in needle):
            return True
        # Compare on the executable-ish stem too ("notepad.exe" vs "notepad").
        stem = self.target.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].split(".")[0]
        return bool(stem) and stem.lower() in needle


# --- module state ------------------------------------------------------------
_launched: list[LaunchedApp] = []
_explicit_depth = 0


# --- ledger ------------------------------------------------------------------
def mark_launched(target: str, title: str = "") -> None:
    """Record an application/window SeedCode just opened."""
    target = (target or "").strip()
    if not target:
        return
    for app in _launched:
        if app.target.lower() == target.lower():
            return
    _launched.append(LaunchedApp(target=target, title=title or target, opened_at=time.time()))
    _log.info("opened '%s' — it will stay open until explicitly closed", target)


def launched_apps() -> list[LaunchedApp]:
    """Applications opened by SeedCode this session (most recent last)."""
    return list(_launched)


def was_launched_by_seedcode(title: str) -> bool:
    return any(app.matches(title) for app in _launched)


# --- explicit intent ---------------------------------------------------------
def closing_explicitly() -> bool:
    """Whether the current call stack is an explicit close request."""
    return _explicit_depth > 0


@contextmanager
def explicit_close() -> Iterator[None]:
    """Mark the enclosed block as an explicit, user-requested close."""
    global _explicit_depth
    _explicit_depth += 1
    try:
        yield
    finally:
        _explicit_depth -= 1


def guard_close(title: str, *, force: bool = False) -> None:
    """Refuse an implicit close; returns None when the close is legitimate.

    Rules, all of them about *intent*, never about the window itself:

    * inside :func:`explicit_close` anything may be closed (the user asked);
    * outside it, a forced kill (``taskkill``) is always refused — a force-kill
      from a cleanup path is exactly the bug being fixed;
    * outside it, closing an application SeedCode opened is refused, because
      that app is still what the user is looking at.
    """
    if closing_explicitly():
        return
    if force:
        raise ImplicitCloseRefused(
            f"Refusing to force-kill '{title}' outside an explicit close "
            "request. Applications opened by Seed Code stay open."
        )
    if was_launched_by_seedcode(title):
        raise ImplicitCloseRefused(
            f"Refusing to close '{title}': Seed Code opened it during this "
            "session and applications stay open unless the user asks to close "
            "them (close_app / close_browser)."
        )


# --- teardown ----------------------------------------------------------------
def release_internal() -> list[str]:
    """Session teardown: release internal resources, never close applications.

    Returns the applications that are deliberately left open, so the caller can
    log them. Nothing here touches a window, a tab, or a process.
    """
    open_apps = [app.title for app in _launched]
    if open_apps:
        _log.info(
            "session cleanup: leaving %d opened application(s) open: %s",
            len(open_apps),
            ", ".join(open_apps[:6]),
        )
    return open_apps


def reset() -> None:
    """Test isolation hook."""
    global _explicit_depth
    _launched.clear()
    _explicit_depth = 0
