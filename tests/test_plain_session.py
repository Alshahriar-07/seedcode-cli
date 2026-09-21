"""Line-based prompt fallback for console-less hosts (v6.2.5).

prompt_toolkit refuses to start where there is no console it can drive —
piped/redirected stdin, CI, and some Git Bash/MSYS sessions. That used to be a
*fatal* startup error (``No Windows console found`` / ``Found xterm-256color,
while expecting a Windows console``); Seed Code now degrades to a plain line
prompt instead, matching the fallback the menus already used.
"""

from __future__ import annotations

import pytest

from seedcode.app import (
    _make_chat_session,
    _plain_text,
    _PlainSession,
    _interactive,
)
from seedcode.ui.textbox import prompt_label


# --- the shim ----------------------------------------------------------------

def test_plain_session_returns_the_typed_line(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda *a, **k: "hello")
    assert _PlainSession().prompt(prompt_label("you > ")) == "hello"


def test_plain_session_shows_the_literal_prompt_text(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_input(prompt: str = "") -> str:
        seen["prompt"] = prompt
        return "x"

    monkeypatch.setattr("builtins.input", fake_input)
    _PlainSession().prompt(prompt_label("you > "))
    assert seen["prompt"] == "you > "


def test_plain_session_propagates_eof(monkeypatch) -> None:
    """EOF must reach the loop, which treats it as Ctrl+D."""

    def boom(*_a, **_k):
        raise EOFError

    monkeypatch.setattr("builtins.input", boom)
    with pytest.raises(EOFError):
        _PlainSession().prompt("> ")


def test_plain_session_propagates_keyboard_interrupt(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", boom)
    with pytest.raises(KeyboardInterrupt):
        _PlainSession().prompt("> ")


# --- message unwrapping -------------------------------------------------------

def test_plain_text_unwraps_prompt_label() -> None:
    assert _plain_text(prompt_label("you > ")) == "you > "


def test_plain_text_passes_through_plain_strings() -> None:
    assert _plain_text("plain") == "plain"


def test_plain_text_is_empty_for_unknown_objects() -> None:
    assert _plain_text(object()) == ""


# --- session selection --------------------------------------------------------

def test_non_interactive_host_gets_the_shim(monkeypatch) -> None:
    monkeypatch.setattr("seedcode.app._interactive", lambda: False)
    assert isinstance(_make_chat_session(None), _PlainSession)


def test_prompt_toolkit_failure_degrades_instead_of_raising(monkeypatch) -> None:
    """The exact production failure: a console exists but cannot be driven."""
    monkeypatch.setattr("seedcode.app._interactive", lambda: True)

    def boom(_ui):
        raise RuntimeError("No Windows console found. Are you running cmd.exe?")

    monkeypatch.setattr("seedcode.app._build_chat_session", boom)
    assert isinstance(_make_chat_session(None), _PlainSession)


def test_interactive_host_keeps_prompt_toolkit(monkeypatch) -> None:
    monkeypatch.setattr("seedcode.app._interactive", lambda: True)
    sentinel = object()
    monkeypatch.setattr("seedcode.app._build_chat_session", lambda _ui: sentinel)
    assert _make_chat_session(None) is sentinel


# --- tty detection ------------------------------------------------------------

def _fake_sys(stdin_tty: bool, stdout_tty: bool):
    class _Stream:
        def __init__(self, tty: bool) -> None:
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

    return type(
        "S", (), {"stdin": _Stream(stdin_tty), "stdout": _Stream(stdout_tty)}
    )()


def test_interactive_requires_both_streams_to_be_a_tty(monkeypatch) -> None:
    monkeypatch.setattr("seedcode.app.sys", _fake_sys(True, True))
    assert _interactive() is True
    monkeypatch.setattr("seedcode.app.sys", _fake_sys(True, False))
    assert _interactive() is False
    monkeypatch.setattr("seedcode.app.sys", _fake_sys(False, True))
    assert _interactive() is False


def test_interactive_is_false_when_streams_have_no_isatty(monkeypatch) -> None:
    monkeypatch.setattr("seedcode.app.sys", type("S", (), {"stdin": None, "stdout": None})())
    assert _interactive() is False
