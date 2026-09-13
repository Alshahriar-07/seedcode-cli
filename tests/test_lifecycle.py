"""Tests for the auto-exit fix: lifecycle state machine + self-guard.

The bug: desktop tasks (closing an app, popup sweeps, hotkey workflows) could
resolve SeedCode's own console window and terminate the host terminal — the
CLI exited after finishing a task. The fix has two layers, both tested here:

* :mod:`seedcode.core.lifecycle` — a centralized state machine where a turn
  always returns to IDLE and only an explicit exit decision reaches SHUTDOWN;
* :mod:`seedcode.computer.selfguard` — window/process identification that
  keeps close/kill/keystroke paths away from SeedCode's own terminal.

Everything runs against fakes; no real window, process, or keystroke is used.
"""

from __future__ import annotations

import pytest

from seedcode.computer import selfguard
from seedcode.core.lifecycle import Lifecycle, LifecycleError, Phase, lifecycle
from seedcode.tools.permissions import PermissionError_, PermissionManager, PermissionLevel


# --- lifecycle state machine ---------------------------------------------------


class TestLifecycle:
    def test_starts_idle(self):
        assert Lifecycle().phase is Phase.IDLE

    def test_full_task_cycle_returns_to_idle(self):
        lc = Lifecycle()
        lc.begin_turn()
        lc.to_executing()
        lc.to_verifying()
        lc.to_responding()
        lc.end_turn()
        assert lc.phase is Phase.IDLE

    def test_error_path_returns_to_idle(self):
        """A failed turn must also land on IDLE — never on SHUTDOWN."""
        lc = Lifecycle()
        lc.begin_turn()
        lc.to_executing()
        lc.end_turn()  # what task_span's finally does on any exception
        assert lc.phase is Phase.IDLE

    def test_responding_can_never_shutdown(self):
        lc = Lifecycle()
        lc.begin_turn()
        lc.to_executing()
        lc.to_verifying()
        lc.to_responding()
        with pytest.raises(LifecycleError):
            lc.shutdown()

    def test_illegal_transition_raises(self):
        lc = Lifecycle()
        with pytest.raises(LifecycleError):
            lc.to_verifying()  # IDLE -> VERIFYING skips the chain

    def test_shutdown_requires_explicit_request(self):
        lc = Lifecycle()
        with pytest.raises(LifecycleError):
            lc.shutdown()

    def test_explicit_exit_reaches_shutdown_from_idle(self):
        lc = Lifecycle()
        lc.request_exit("test")
        lc.shutdown()
        assert lc.phase is Phase.SHUTDOWN
        assert not lc.is_running()

    def test_shutdown_is_terminal(self):
        lc = Lifecycle()
        lc.request_exit()
        lc.shutdown()
        with pytest.raises(LifecycleError):
            lc.begin_turn()

    def test_shutdown_hooks_run(self):
        lc = Lifecycle()
        ran = []
        lc.on_shutdown(lambda: ran.append(1))
        lc.request_exit()
        lc.shutdown()
        assert ran == [1]

    def test_broken_hook_does_not_block_exit(self):
        lc = Lifecycle()
        lc.on_shutdown(lambda: 1 / 0)
        lc.request_exit()
        lc.shutdown()  # must not raise
        assert lc.phase is Phase.SHUTDOWN

    def test_task_span_always_returns_to_idle(self):
        lc = Lifecycle()
        # The span re-raises (the REPL catches it); the state still lands IDLE.
        with pytest.raises(RuntimeError):
            with lc.task_span():
                lc.to_executing()
                raise RuntimeError("tool exploded")
        assert lc.phase is Phase.IDLE  # the exception didn't strand the state

    def test_task_span_propagates_exceptions(self):
        lc = Lifecycle()
        with pytest.raises(RuntimeError):
            with lc.task_span():
                raise RuntimeError("seen by the REPL")

    def test_nested_end_turn_is_idempotent(self):
        lc = Lifecycle()
        lc.begin_turn()
        lc.end_turn()
        lc.end_turn()  # a doubled finally must not raise
        assert lc.phase is Phase.IDLE


# --- the process-wide instance ---------------------------------------------------


class TestProcessLifecycle:
    def test_singleton(self):
        assert lifecycle() is lifecycle()

    def test_reset_for_tests(self):
        lc = lifecycle()
        lc.reset_for_tests()
        assert lc.phase is Phase.IDLE and not lc.exit_requested


# --- self-guard: process identification ------------------------------------------


class TestSelfGuardProcesses:
    def test_own_pid_is_own(self):
        assert selfguard.is_own_process(selfguard.own_pid()) is True

    def test_bogus_pid_is_not_own(self):
        assert selfguard.is_own_process(999_999_999) is False

    def test_junk_pid_is_not_own(self):
        assert selfguard.is_own_process("not-a-pid") is False

    def test_ancestors_include_self(self):
        assert selfguard.own_pid() in selfguard.ancestor_pids()


# --- self-guard: window identification -------------------------------------------


class FakeWindow:
    """A pygetwindow-style stand-in."""

    def __init__(self, title: str, hwnd=None):
        self.title = title
        self._hWnd = hwnd


class TestSelfGuardWindows:
    def test_exact_console_title_is_own(self, monkeypatch):
        monkeypatch.setattr(selfguard, "console_title", lambda: "SeedCode — Terminal")
        assert selfguard.is_own_title("SeedCode — Terminal") is True

    def test_different_title_is_not_own(self, monkeypatch):
        monkeypatch.setattr(selfguard, "console_title", lambda: "SeedCode — Terminal")
        assert selfguard.is_own_title("YouTube - Google Chrome") is False

    def test_substring_is_NOT_own(self, monkeypatch):
        """Only an exact title match counts: other consoles must stay closable."""
        monkeypatch.setattr(selfguard, "console_title", lambda: "Command Prompt")
        assert selfguard.is_own_title("Command Prompt - python seedcode") is False

    def test_empty_title_is_not_own(self, monkeypatch):
        monkeypatch.setattr(selfguard, "console_title", lambda: "SeedCode")
        assert selfguard.is_own_title("") is False

    def test_window_object_via_hwnd(self, monkeypatch):
        monkeypatch.setattr(selfguard, "console_hwnd", lambda: 4242)
        assert selfguard.is_own_window(FakeWindow("whatever", hwnd=4242)) is True
        assert selfguard.is_own_window(FakeWindow("whatever", hwnd=99)) is False

    def test_window_object_via_exact_title(self, monkeypatch):
        monkeypatch.setattr(selfguard, "console_title", lambda: "SeedCode")
        assert selfguard.is_own_window(FakeWindow("SeedCode")) is True
        assert selfguard.is_own_window(FakeWindow("SeedCode docs")) is False

    def test_detached_console_is_never_own(self, monkeypatch):
        """No console (GUI mode / CI): nothing can be misidentified as ours."""
        monkeypatch.setattr(selfguard, "console_hwnd", lambda: 0)
        monkeypatch.setattr(selfguard, "console_title", lambda: "")
        assert selfguard.is_own_window(FakeWindow("Command Prompt")) is False
        assert selfguard.is_own_title("Command Prompt") is False


# --- windows driver: close/focus can't reach our terminal --------------------------


class TestWindowsDriverGuard:
    def _driver(self, monkeypatch, windows):
        from seedcode.computer import windows as win_driver

        monkeypatch.setattr(win_driver, "_gw", lambda: windows)
        monkeypatch.setattr(win_driver.selfguard, "console_title", lambda: "Terminal — SeedCode")
        return win_driver

    class _GW:
        def __init__(self, wins):
            self._wins = wins

        def getAllWindows(self):
            return self._wins

        def getActiveWindow(self):
            return self._wins[0] if self._wins else None

    class _Win:
        def __init__(self, title):
            self.title = title
            self.closed = False
            self.activated = False

        def close(self):
            self.closed = True

        def activate(self):
            self.activated = True

        def restore(self):
            pass

        @property
        def isMinimized(self):
            return False

    def test_find_skips_own_console(self, monkeypatch):
        """The only window matching the needle is ours -> nothing is found."""
        win_driver = self._driver(monkeypatch, self._GW([self._Win("Terminal — SeedCode")]))
        with pytest.raises(ValueError, match="No window found"):
            win_driver._find("terminal")

    def test_close_own_title_refuses(self, monkeypatch):
        win_driver = self._driver(monkeypatch, self._GW([]))
        with pytest.raises(ValueError, match="Refusing to close SeedCode"):
            win_driver.close_window("Terminal — SeedCode")

    def test_close_external_window_still_works(self, monkeypatch):
        target = self._Win("YouTube - Google Chrome")
        win_driver = self._driver(
            monkeypatch, self._GW([self._Win("Terminal — SeedCode"), target])
        )
        message = win_driver.close_window("YouTube")
        assert "Closed" in message
        assert target.closed is True

    def test_close_skips_own_and_finds_external(self, monkeypatch):
        """A needle matching both must close the external one, never ours."""
        own = self._Win("Terminal — SeedCode")
        other = self._Win("Terminal — settings")  # also matches "terminal"
        win_driver = self._driver(monkeypatch, self._GW([own, other]))
        win_driver.close_window("terminal")
        assert own.closed is False and other.closed is True

    def test_focus_cannot_target_own_console(self, monkeypatch):
        own = self._Win("Terminal — SeedCode")
        win_driver = self._driver(monkeypatch, self._GW([own]))
        with pytest.raises(ValueError):
            win_driver.focus_window("terminal")
        assert own.activated is False


# --- keyboard driver: no window-closing combos, no typing into ourselves ----------


class TestKeyboardGuard:
    def test_alt_f4_refused(self):
        from seedcode.computer import keyboard

        with pytest.raises(ValueError, match="window-closing hotkey"):
            keyboard.hotkey(["alt", "f4"])

    def test_ctrl_shift_w_refused(self):
        from seedcode.computer import keyboard

        with pytest.raises(ValueError, match="window-closing hotkey"):
            keyboard.hotkey(["ctrl", "shift", "w"])

    def test_alt_f4_refused_even_with_extra_keys(self):
        from seedcode.computer import keyboard

        with pytest.raises(ValueError):
            keyboard.hotkey(["ctrl", "alt", "f4"])

    def test_normal_hotkeys_pass_validation(self, monkeypatch):
        from seedcode.computer import keyboard

        # Validation + guards run; the pyautogui call itself is mocked away.
        # (When running the suite in a real console, the foreground window IS
        # ours — so the focus guard must be pinned off for this test.)
        monkeypatch.setattr(selfguard, "foreground_is_own", lambda: False)

        class _FakeGui:
            def __init__(self):
                self.hotkeys = []

            def hotkey(self, *keys):
                self.hotkeys.append(keys)

        gui = _FakeGui()
        monkeypatch.setattr(keyboard, "_pyautogui", lambda: gui)
        keyboard.hotkey(["ctrl", "s"])  # must not raise
        assert gui.hotkeys == [("ctrl", "s")]

    def test_typing_refused_when_own_window_focused(self, monkeypatch):
        from seedcode.computer import keyboard

        monkeypatch.setattr(selfguard, "foreground_is_own", lambda: True)
        with pytest.raises(ValueError, match="own window is focused"):
            keyboard.type_text("hello")

    def test_hotkeys_refused_when_own_window_focused(self, monkeypatch):
        from seedcode.computer import keyboard

        monkeypatch.setattr(selfguard, "foreground_is_own", lambda: True)
        with pytest.raises(ValueError, match="own window is focused"):
            keyboard.hotkey(["esc"])

    def test_typing_allowed_when_other_window_focused(self, monkeypatch):
        from seedcode.computer import keyboard

        monkeypatch.setattr(selfguard, "foreground_is_own", lambda: False)

        class _FakeGui:
            def __init__(self):
                self.typed = []

            def typewrite(self, text, interval=0.02):
                self.typed.append(text)

        gui = _FakeGui()
        monkeypatch.setattr(keyboard, "_pyautogui", lambda: gui)
        keyboard.type_text("hello")  # must not raise
        assert gui.typed == ["hello"]


# --- integration: a tool failure can never kill the session ------------------------


class TestSessionSurvivesTaskFailure:
    def make_manager(self, tmp_path):
        return PermissionManager(workspace=tmp_path, level=PermissionLevel.DESKTOP)

    def test_desktop_error_is_permission_error_not_exit(self, tmp_path):
        """Permission refusals surface as errors the model can read."""
        manager = self.make_manager(tmp_path)
        with pytest.raises(PermissionError_):
            manager.require(PermissionLevel.FULL_SYSTEM, "dangerous thing")

    def test_lifecycle_survives_a_crashing_turn(self, tmp_path):
        """Simulate: REPL -> turn raises -> REPL continues. The core guarantee."""
        lc = lifecycle()
        lc.reset_for_tests()
        turns_survived = 0
        for _ in range(3):
            try:
                with lc.task_span():
                    raise RuntimeError("task failed")
            except RuntimeError:
                turns_survived += 1  # REPL catches, keeps prompting
        assert turns_survived == 3
        assert lc.phase is Phase.IDLE
        assert lc.is_running()  # the app is still alive

    def test_exit_only_via_explicit_request(self):
        lc = lifecycle()
        lc.reset_for_tests()
        # Three failing turns...
        for _ in range(3):
            with pytest.raises(RuntimeError):
                with lc.task_span():
                    raise RuntimeError("fail")
        assert lc.is_running()
        # ...then one explicit exit decision.
        lc.request_exit("user typed exit")
        lc.shutdown()
        assert not lc.is_running()
