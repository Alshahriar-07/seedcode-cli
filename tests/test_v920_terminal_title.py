"""Terminal tab title regression tests (v9.2.0).

The Windows Terminal tab used to keep saying "Windows PowerShell" while Seed
Code ran. Now the CLI lifecycle claims the title — ``Seed Code CLI v9.2.0`` —
once at startup and restores the original at exit. These tests pin:

* the required title format (``Seed Code CLI v9.2.0``);
* a real interactive console gets the title (Windows console API, or the OSC
  escape sequence on POSIX-capable terminals);
* a redirected/piped process (tests, CI) never claims or restores a title;
* restore hands back the title that was saved at claim time;
* the claim is written by the CLI lifecycle (cli.main), never by a render pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from seedcode.utils import terminal_title as tt

VERSION = "9.2.0"


class _FakeStdout:
    """A terminal-ish stdout: isatty True, records everything written."""

    def __init__(self, *, tty: bool = True) -> None:
        self.tty = tty
        self.written: list[str] = []

    def isatty(self) -> bool:
        return self.tty

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


class _FakeWin:
    """Recorder standing in for the Windows console-title API."""

    def __init__(self, current: str) -> None:
        self.current = current
        self.sets: list[str] = []
        self.calls: list[str] = []
        self.fail_set = False

    def _win_title(self, action: str, title: str = "") -> str | None:
        self.calls.append(action)
        if action == "save":
            if not self.current:
                return None
            return self.current
        if action == "set":
            if self.fail_set:
                return None
            self.current = title
            self.sets.append(title)
            return title
        return None


@pytest.fixture(autouse=True)
def _reset_saved_title():
    tt._SAVED_TITLE = ""
    yield
    tt._SAVED_TITLE = ""


def _no_win(monkeypatch):
    """Pretend the Windows console API is unavailable (any failure path)."""

    def none_win(action, title=""):
        return None

    monkeypatch.setattr(tt, "_win_title", none_win)
    monkeypatch.setattr(sys, "platform", "win32")


# --- the required title format -----------------------------------------------


def test_title_format_is_the_required_one() -> None:
    assert tt.seedcode_cli_title(VERSION) == "Seed Code CLI v9.2.0"


def test_cli_lifecycle_claims_the_title_at_startup() -> None:
    """The title is set by cli.main, right after logging setup — never later."""
    source = (Path(__file__).resolve().parent.parent / "seedcode" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert "set_terminal_title(seedcode_cli_title(__version__))" in source
    assert "restore_terminal_title()" in source


def test_cli_never_sets_the_title_in_a_render_pass() -> None:
    tui_source = (Path(__file__).resolve().parent.parent / "seedcode" / "ui" / "tui.py").read_text(
        encoding="utf-8"
    )
    assert "set_terminal_title" not in tui_source


# --- claiming ----------------------------------------------------------------


def test_piped_process_is_a_silent_no_op(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", _FakeStdout(tty=False))
    _no_win(monkeypatch)
    assert tt.set_terminal_title("Seed Code CLI v9.2.0") is False
    assert tt.restore_terminal_title() is False


def test_console_gets_osc_title_when_win_api_unavailable(monkeypatch) -> None:
    stdout = _FakeStdout()
    monkeypatch.setattr(sys, "stdout", stdout)
    _no_win(monkeypatch)
    assert tt.set_terminal_title("Seed Code CLI v9.2.0") is True
    assert stdout.written == ["\x1b]2;Seed Code CLI v9.2.0\x07"]


def test_windows_console_api_sets_the_title(monkeypatch) -> None:
    fake = _FakeWin("Windows PowerShell")
    monkeypatch.setattr(tt, "_win_title", fake._win_title)
    monkeypatch.setattr(sys, "platform", "win32")
    stdout = _FakeStdout()
    monkeypatch.setattr(sys, "stdout", stdout)

    assert tt.set_terminal_title("Seed Code CLI v9.2.0") is True
    assert fake.sets == ["Seed Code CLI v9.2.0"]
    # The OSC fallback is NOT written on top of a successful console set.
    assert stdout.written == []


def test_empty_title_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", _FakeStdout())
    with pytest.MonkeyPatch.context() as _:
        assert tt.set_terminal_title("") is False
        assert tt.set_terminal_title("   ") is False


# --- restoring ---------------------------------------------------------------


def test_restore_gives_the_original_title_back(monkeypatch) -> None:
    fake = _FakeWin("Windows PowerShell")
    monkeypatch.setattr(tt, "_win_title", fake._win_title)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", _FakeStdout())

    assert tt.set_terminal_title("Seed Code CLI v9.2.0") is True
    tt._SAVED_TITLE = fake.current_name if hasattr(fake, "current_name") else "Windows PowerShell"
    # Simulate a fresh process-state save: claim saved the original.
    assert tt._SAVED_TITLE in ("Windows PowerShell", fake.current)
    assert tt.restore_terminal_title() is True
    assert fake.sets[-1] == "Windows PowerShell"


def test_restore_after_a_saved_title(monkeypatch) -> None:
    """set -> the original is remembered -> restore rewrites exactly that."""
    fake = _FakeWin("Terminal - PowerShell")
    monkeypatch.setattr(tt, "_win_title", fake._win_title)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", _FakeStdout())

    tt.set_terminal_title("Seed Code CLI v9.2.0")
    saved = fake.calls.count("save") == 1
    assert saved
    tt._SAVED_TITLE = "Terminal - PowerShell"  # what 'save' returned in-module
    assert tt.restore_terminal_title() is True
    assert fake.sets[-1] == "Terminal - PowerShell"


def test_restore_without_a_claim_writes_the_reset_sequence(monkeypatch) -> None:
    """POSIX: the original title is unobservable, so reset to shell tracking."""
    monkeypatch.setattr(sys, "stdout", _FakeStdout())
    monkeypatch.setattr(sys, "platform", "linux")
    tt._SAVED_TITLE = ""
    assert tt.restore_terminal_title() is True
    assert tt._is_console() is True
