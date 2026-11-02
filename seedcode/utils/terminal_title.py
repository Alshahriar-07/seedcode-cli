"""Terminal tab title (v9.2.0): claimed at startup, restored at exit.

Windows Terminal and classic consoles both carry a window/tab title. Seed Code
claims it — ``Seed Code CLI v9.2.0`` — once per session from the CLI
lifecycle, and hands the original title back on exit. It is never called from
a UI render pass, so a repaint can never rewrite it, and the module degrades
to a no-op in every environment that cannot hold a title:

* pipes and redirected output (tests, CI, ``| tee``): nothing is written;
* POSIX terminals: the OSC title sequence is written only to a real terminal,
  and the original title cannot be read back, so only the claim is possible;
* Windows: the title is set — and restored on exit — through the console API,
  which Windows Terminal reflects in its tab and conhost in its window.
"""

from __future__ import annotations

import sys

__all__ = ["seedcode_cli_title", "set_terminal_title", "restore_terminal_title"]

_SAVED_TITLE = ""
_OSC_TITLE = "\x1b]2;{title}\x07"
_OSC_RESET = "\x1b]2;\x07"


def seedcode_cli_title(version: str) -> str:
    """The startup title: ``Seed Code CLI v9.2.0`` (the spec's required form)."""
    return f"Seed Code CLI v{version}"


def _is_console() -> bool:
    """True when the process owns an interactive terminal it may retitle."""
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _win_title(action: str, title: str = "") -> str | None:
    """Touch the Windows console title; returns the saved title on 'save'.

    Returns ``None`` on any failure — a real console is required, so a
    redirected process never claims (or restores) a title it does not have.
    """
    import ctypes

    try:
        kernel32 = ctypes.windll.kernel32
        if action == "save":
            buffer = ctypes.create_unicode_buffer(512)
            if kernel32.GetConsoleTitleW(buffer, 512) == 0:
                return None
            return buffer.value
        if action == "set":
            ok = int(kernel32.SetConsoleTitleW(title)) != 0
            return None if not ok else title
    except Exception:
        return None
    return None


def set_terminal_title(title: str) -> bool:
    """Claim the terminal tab title for this session (once, at startup).

    The original title is saved for :func:`restore_terminal_title`. Returns
    True when a title was actually set; a title-less environment (pipes,
    tests, ``SEEDCODE_NO_TUI`` batch runs) is a silent no-op.
    """
    global _SAVED_TITLE
    if not _is_console() or not (title or "").strip():
        return False
    _SAVED_TITLE = ""
    is_windows = sys.platform == "win32"

    saved = _win_title("save") if is_windows else None
    if saved is not None:
        _SAVED_TITLE = saved
    elif is_windows:
        # No console title readable (redirected handle): still claim visually.
        pass

    if is_windows:
        if _win_title("set", title) is not None:
            return True
    # OSC fallback (also the POSIX path): only when the console takes escapes.
    try:
        if _is_console() and hasattr(sys.stdout, "write"):
            sys.stdout.write(_OSC_TITLE.format(title=title))
            sys.stdout.flush()
            return True
    except Exception:
        return False
    return False


def restore_terminal_title() -> bool:
    """Hand the original terminal title back (best effort, on exit).

    Windows can read the original title at claim time and restore it exactly.
    On POSIX the original title is not observable, so the reset sequence hands
    the tab back to the shell's own title tracking instead.
    """
    if not _is_console():
        return False
    is_windows = sys.platform == "win32"
    if is_windows and _SAVED_TITLE:
        if _win_title("set", _SAVED_TITLE) is not None:
            return True
    try:
        if hasattr(sys.stdout, "write"):
            sys.stdout.write(_OSC_RESET)
            sys.stdout.flush()
            return True
    except Exception:
        return False
    return False
