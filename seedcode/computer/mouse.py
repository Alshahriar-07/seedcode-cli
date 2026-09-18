"""Mouse driver: move, click, drag, scroll via pyautogui.

Coordinate validation lives in the controller (which knows the screen
geometry); this module only performs the raw actions. ``FAILSAFE`` stays on:
slamming the pointer into the top-left corner aborts any action — the user's
emergency brake.
"""

from __future__ import annotations


def _pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = True
    # v6.2.0: 0.05 -> 0.01. The per-call PAUSE was the single biggest fixed
    # cost of every click/type/hotkey; 10ms still settles Win32 input queues
    # while cutting multi-step action chains by ~40ms per action.
    pyautogui.PAUSE = 0.01
    return pyautogui


def position() -> tuple[int, int]:
    """Current pointer position."""
    point = _pyautogui().position()
    return int(point.x), int(point.y)


def move(x: int, y: int, duration: float = 0.08) -> None:
    """Move the pointer; ``duration`` is a smooth-glide time, not a sleep.

    v6.2.0: default glide 0.2s -> 0.08s. UIA-resolved targets already carry
    exact coordinates, so a long animated glide is wasted motion; the shorter
    glide still avoids teleport artifacts in apps that animate hover states.
    """
    _pyautogui().moveTo(x, y, duration=duration)


def click(x: int, y: int, button: str = "left", double: bool = False) -> None:
    gui = _pyautogui()
    clicks = 2 if double else 1
    gui.click(x=x, y=y, clicks=clicks, button=button)


def drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.25) -> None:
    """Drag & drop: press at (x1, y1), release at (x2, y2)."""
    gui = _pyautogui()
    gui.moveTo(x1, y1, duration=0.08)
    gui.dragTo(x2, y2, duration=max(0.1, duration), button="left")


def scroll(amount: int, x: int | None = None, y: int | None = None) -> None:
    """Scroll by ``amount`` notches (positive = up) at an optional position."""
    gui = _pyautogui()
    if x is not None and y is not None:
        gui.moveTo(x, y, duration=0.08)
    gui.scroll(amount)
