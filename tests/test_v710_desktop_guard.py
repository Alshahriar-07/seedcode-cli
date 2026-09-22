"""v7.1.0 regression: an application Seed Code opened must stay open.

The reported behaviour was: open an app, do the work, and the app closes. These
tests pin the fix at every layer that could close something:

* the lifecycle guard (implicit closes refused, explicit closes allowed);
* the window driver (``close_window``), including the ``taskkill`` force path;
* the browser driver and the browser engine (no blind ``Ctrl+W``);
* the app's session teardown hook (internal cleanup only);
* the Computer Engine controller (the one legitimate close path).

Legitimate internal cleanup — releasing mouse/keyboard control, closing
DevTools sockets, dropping cached engines — is untouched, and an explicit
user-requested close still works.
"""

from __future__ import annotations

import sys

import pytest

from seedcode.computer import lifecycle_guard
from seedcode.computer.lifecycle_guard import (
    ImplicitCloseRefused,
    closing_explicitly,
    explicit_close,
    guard_close,
    launched_apps,
    mark_launched,
)


@pytest.fixture(autouse=True)
def _clean_guard():
    lifecycle_guard.reset()
    yield
    lifecycle_guard.reset()


# --- the guard itself ---------------------------------------------------------


def test_opening_an_app_is_remembered() -> None:
    mark_launched("notepad")
    mark_launched("notepad")  # idempotent
    mark_launched("Google Chrome")
    assert [app.target for app in launched_apps()] == ["notepad", "Google Chrome"]


def test_implicit_close_of_an_opened_app_is_refused() -> None:
    mark_launched("Google Chrome")
    with pytest.raises(ImplicitCloseRefused, match="stay open"):
        guard_close("YouTube - Google Chrome")


def test_explicit_close_is_allowed() -> None:
    mark_launched("notepad")
    assert closing_explicitly() is False
    with explicit_close():
        assert closing_explicitly() is True
        guard_close("Untitled - Notepad")  # no raise: the user asked for it
    assert closing_explicitly() is False


def test_force_kill_is_refused_outside_an_explicit_request() -> None:
    with pytest.raises(ImplicitCloseRefused, match="force-kill"):
        guard_close("notepad", force=True)
    with explicit_close():
        guard_close("notepad", force=True)  # fine: explicitly requested


def test_closing_an_unrelated_window_is_not_blocked_by_the_guard() -> None:
    # The guard is about intent, not about forbidding every close: a window we
    # never opened is not protected by the launch ledger.
    guard_close("Some other app")


def test_release_internal_reports_but_never_closes_apps() -> None:
    mark_launched("notepad")
    mark_launched("Google Chrome")
    left_open = lifecycle_guard.release_internal()
    assert left_open == ["notepad", "Google Chrome"]
    assert len(launched_apps()) == 2  # nothing was closed


# --- the window driver --------------------------------------------------------


class _Win:
    def __init__(self, title: str) -> None:
        self.title = title
        self.left = self.top = 0
        self.width = self.height = 100
        self.isMinimized = False
        self._hWnd = 0
        self.closed = False
        self.activated = False

    def close(self) -> None:
        self.closed = True

    def activate(self) -> None:
        self.activated = True

    def restore(self) -> None:
        pass


class _GW:
    def __init__(self, windows: list[_Win]) -> None:
        self._windows = windows
        self._active = windows[0] if windows else None

    def getAllWindows(self):
        return list(self._windows)

    def getActiveWindow(self):
        return self._active


def _driver(monkeypatch, fake_gw):
    from seedcode.computer import windows as win_driver

    monkeypatch.setattr(win_driver, "_gw", lambda: fake_gw)
    # The self-guard compares against the hosting console's real title; pin it
    # so the test is deterministic on any machine.
    monkeypatch.setattr(
        win_driver.selfguard, "console_title", lambda: "Terminal — SeedCode"
    )
    return win_driver


def test_open_app_records_the_launch(monkeypatch) -> None:
    import os

    from seedcode.computer import windows as win_driver

    monkeypatch.setattr(os, "startfile", lambda target: None, raising=False)
    win_driver.open_app("notepad")
    assert "notepad" in [app.target for app in launched_apps()]


def test_close_window_refuses_an_app_seedcode_opened(monkeypatch) -> None:
    browser = _Win("YouTube - Google Chrome")
    driver = _driver(monkeypatch, _GW([browser]))
    mark_launched("Google Chrome")

    with pytest.raises(ImplicitCloseRefused):
        driver.close_window("YouTube")
    assert browser.closed is False  # the browser is still open


def test_close_window_still_works_when_explicitly_requested(monkeypatch) -> None:
    browser = _Win("YouTube - Google Chrome")
    driver = _driver(monkeypatch, _GW([browser]))
    mark_launched("Google Chrome")

    with explicit_close():
        message = driver.close_window("YouTube")
    assert "Closed" in message
    assert browser.closed is True


def test_force_close_of_a_seedcode_opened_app_is_refused(monkeypatch) -> None:
    app = _Win("Untitled - Notepad")
    driver = _driver(monkeypatch, _GW([app]))
    mark_launched("notepad")
    with pytest.raises(ImplicitCloseRefused):
        driver.close_window("Notepad", force=True)
    assert app.closed is False


def test_selfguard_still_wins_over_everything(monkeypatch) -> None:
    driver = _driver(monkeypatch, _GW([]))
    with pytest.raises(ValueError, match="Refusing to close SeedCode"):
        driver.close_window("Terminal — SeedCode")


# --- the browser driver / engine ---------------------------------------------


def test_browser_navigate_records_the_browser_launch(monkeypatch) -> None:
    from seedcode.computer import browser

    monkeypatch.setattr(browser, "_open", lambda target, new_window=False: True)
    browser.navigate("https://example.com")
    assert launched_apps(), "opening a URL must record the browser as opened"


def test_browser_close_is_explicit_and_guarded(monkeypatch) -> None:
    from seedcode.computer import browser, windows as win_driver

    browser._active_browser_title = lambda: "Example - Google Chrome"  # type: ignore[assignment]
    mark_launched("Google Chrome")
    calls: list[str] = []
    monkeypatch.setattr(win_driver, "close_window", lambda title, force=False: calls.append(title))

    # Explicit close: allowed and it really calls the driver.
    assert "Closed" in browser.close_browser()
    assert calls == ["Example - Google Chrome"]


def test_controller_close_app_is_the_explicit_path(monkeypatch) -> None:
    from seedcode.computer import controller as controller_mod

    recorded: list[tuple[str, bool]] = []

    class _Windows:
        def __init__(self) -> None:
            self.active_window = lambda: None

        def close_window(self, title, force=False):
            recorded.append((title, force))
            return f"Closed '{title}'."

    ctl = controller_mod.ComputerController(windows=_Windows())
    mark_launched("notepad")
    message = ctl.close_app("Notepad")
    assert "Closed" in message
    assert recorded == [("Notepad", False)]


def test_browser_engine_never_sends_blind_ctrl_w(monkeypatch) -> None:
    from seedcode.computer.browser_engine import BrowserEngine, BrowserWorkflowError

    hotkeys: list[tuple[str, ...]] = []

    class _Controller:
        def hotkey(self, keys):
            hotkeys.append(tuple(keys))

        def focus_window(self, title):
            return "focused"

    class _Cdp:
        def is_available(self):
            return False

    engine = BrowserEngine(controller=_Controller(), cdp=_Cdp(), popups=_NoPopups())
    with pytest.raises(BrowserWorkflowError):
        engine.close_tab()
    assert ("ctrl", "w") not in hotkeys


class _NoPopups:
    def sweep(self, max_passes=2):  # pragma: no cover - shape compatibility
        from seedcode.computer.browser_popups import SweepResult

        return SweepResult()


def test_teardown_hook_never_closes_an_application(monkeypatch) -> None:
    """The app's session teardown must free resources without closing apps."""
    from seedcode import app
    from seedcode.computer import windows as win_driver

    mark_launched("notepad")
    closed: list[str] = []
    monkeypatch.setattr(
        win_driver, "close_window", lambda title, force=False: closed.append(title)
    )

    app._release_desktop_resources()

    assert closed == []  # nothing was closed
    assert len(launched_apps()) == 1  # the app stays open


def test_playing_a_song_leaves_the_browser_open() -> None:
    """The reported scenario, end to end at the guard level.

    ``youtube_play`` opens the browser via the driver; when the workflow
    finishes, nothing in any cleanup path may close it.
    """
    from seedcode.computer import browser

    mark_launched(browser.default_browser().name)
    # A cleanup path (whoever it is) tries to tidy up:
    with pytest.raises(ImplicitCloseRefused):
        guard_close(browser.default_browser().name)
    # ...and the workflow's own result handling never touches windows.
    left_open = lifecycle_guard.release_internal()
    assert left_open


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific driver")
def test_windows_signature_is_unchanged() -> None:
    from seedcode.computer import windows as win_driver

    assert callable(win_driver.close_window)
    assert win_driver.close_window.__defaults__ == (False,)
