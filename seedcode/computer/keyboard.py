"""Keyboard driver: text typing and hotkeys via pyautogui.

Typing uses a small per-key interval so target applications reliably receive
every keystroke; hotkeys accept the pyautogui key-name vocabulary
("ctrl", "alt", "shift", "win", "enter", "f5", single characters, ...).

**Self-protection:** synthesized input goes to whatever window currently has
focus. When a browser/app focus step silently failed, that window is often
SeedCode's own terminal — so keystrokes meant for a web page would land in
the user's prompt or, worse, an Alt+F4-style combo would close our console
(the reported auto-exit bug). Two guards close both holes: window-closing
combos are refused outright, and any input is refused while *our* window is
focused (see :mod:`.selfguard`).
"""

from __future__ import annotations

from . import selfguard

# Bound one type_text call: pathological lengths point at a confused model.
MAX_TEXT_LENGTH = 5_000

# Keys that can dismiss or destroy a window. SeedCode never synthesizes
# window-level close/dismiss keystrokes: closing an app is done through
# windows.close_window (guarded), closing a browser tab through the browser
# engine — never by pressing Alt+F4/Cmd+W blind at whatever has focus.
_FORBIDDEN_COMBOS = (
    {"alt", "f4"},      # close the focused window
    {"cmd", "w"},       # macOS-style window close
    {"ctrl", "shift", "w"},  # close all browser windows (not a plain tab)
)

# Keys allowed in hotkey combos (a safety vocabulary, not an exhaustive list
# of what pyautogui supports — unknown names are rejected loudly).
_MODIFIERS = {"ctrl", "alt", "shift", "win", "cmd", "fn"}
_NAMED_KEYS = {
    "enter", "return", "tab", "space", "backspace", "delete", "del", "esc",
    "escape", "home", "end", "pageup", "pagedown", "up", "down", "left",
    "right", "insert", "printscreen", "capslock", "numlock",
} | {f"f{i}" for i in range(1, 25)}


def _pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = True
    return pyautogui


def validate_keys(keys: list[str]) -> list[str]:
    """Normalise and validate hotkey names; raises ValueError on junk."""
    cleaned = []
    for key in keys:
        name = str(key).strip().lower()
        if not name:
            continue
        if name in _MODIFIERS or name in _NAMED_KEYS or len(name) == 1:
            cleaned.append(name)
        else:
            raise ValueError(f"Unknown key '{key}' in hotkey combination.")
    if not cleaned:
        raise ValueError("Hotkey combination is empty.")
    return cleaned


def _assert_safe_input() -> None:
    """Refuse synthesized input while SeedCode's own window has focus.

    A keystroke aimed at another application whose focus step failed must
    never be delivered into the agent's own prompt or console.
    """
    if selfguard.foreground_is_own():
        raise ValueError(
            "SeedCode's own window is focused; refusing to send keystrokes. "
            "Focus the target application first (desktop focus_app / "
            "focus_window)."
        )


def type_text(text: str, interval: float = 0.008) -> None:
    """Type ``text``; ``interval`` is the per-key delay.

    v6.2.0: 20ms -> 8ms per key. Win32 synthesized input is processed
    asynchronously by the target app, so 8ms still delivers every keystroke
    in order while making long text entry (URLs, search queries, code) about
    2.5x faster. Very short intervals caused dropped keys in some Java apps;
    8ms keeps a safety margin.
    """
    _assert_safe_input()
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(
            f"Text is too long to type ({len(text)} chars; max {MAX_TEXT_LENGTH})."
        )
    _pyautogui().typewrite(text, interval=interval)


def hotkey(keys: list[str]) -> None:
    normalized = [k.strip().lower() for k in validate_keys(keys)]
    combo = {k for k in normalized if k not in ("",)}
    for forbidden in _FORBIDDEN_COMBOS:
        if forbidden <= combo:
            raise ValueError(
                "Refusing to send a window-closing hotkey ("
                + "+".join(sorted(forbidden))
                + "). Close applications via the close_app skill or windows "
                "driver instead — never with a blind keyboard shortcut."
            )
    _assert_safe_input()
    _pyautogui().hotkey(*normalized)
