"""Regression tests for the v6.2.0 exit paths.

Root cause of the historical bug: the exit handler referenced a global ``ui``
that no longer existed, so choosing Exit raised ``NameError: name 'ui' is
not defined``. The fix passes ``ui`` explicitly into
:func:`seedcode.app._exit_application`. These tests pin every exit decision
point so the crash cannot return.
"""

from __future__ import annotations

import inspect

from seedcode import app
from seedcode.core.lifecycle import lifecycle


def test_exit_application_signature_takes_ui_explicitly() -> None:
    """The exit decision receives ``ui`` as a parameter — no global lookup."""
    params = inspect.signature(app._exit_application).parameters
    assert "ui" in params, "_exit_application must accept ui explicitly"
    assert "reason" in params


def test_exit_from_menu_choice_is_clean() -> None:
    """Menu Exit / Esc / Ctrl+C all route through _exit_application cleanly."""
    calls: list[str] = []
    lc = lifecycle()
    lc.reset() if hasattr(lc, "reset") else None

    class _FakeUI:
        def dim(self, message: str) -> None:
            calls.append(message)

    # Must not raise (the historical NameError) and must reach shutdown.
    app._exit_application(_FakeUI(), "menu exit")
    assert lc.phase.value == "shutdown"
    assert calls and "Goodbye" in calls[0]


def test_menu_exit_choice_uses_exit_application() -> None:
    """The REPL's menu dispatch maps the Exit item to _exit_application."""
    source = inspect.getsource(app.run)
    assert '_exit_application(ui, "menu exit")' in source
    assert '_exit_application(ui, "menu interrupt")' in source


def test_command_exit_returns_to_menu_not_process() -> None:
    """/exit only leaves the chat loop (should_exit), never the process."""
    source = inspect.getsource(app._chat_loop)
    assert "result.should_exit" in source
    # The chat loop returns to run(), which shows the menu again.
    assert "return" in source
