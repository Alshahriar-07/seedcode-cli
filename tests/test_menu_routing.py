"""Tests for the interactive menu wiring and command-error UX.

The menu must not own business logic: selecting an item has to reach the same
handlers as the equivalent slash command. These tests assert that routing and
that the reference shortcuts are attached to the reference actions.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from seedcode.app import _dispatch_command, _main_menu, _report_command_error
from seedcode.core.models import AppConfig


class _StubUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.panels: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(str(message))

    def dim(self, message: str) -> None:
        self.messages.append(str(message))

    def success(self, message: str) -> None:
        self.messages.append(str(message))

    def warning(self, message: str) -> None:
        self.messages.append(str(message))

    def error(self, message: str) -> None:
        self.messages.append(str(message))

    def blank(self) -> None:
        self.messages.append("")

    def panel(self, body, title: str | None = None) -> None:
        self.panels.append(title or "")


# --- menu structure ----------------------------------------------------------

def test_main_menu_has_reference_items_and_shortcuts(monkeypatch) -> None:
    import seedcode.app as app

    captured: dict = {}

    def fake_run_menu(items, **kwargs):
        captured["items"] = list(items)
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(app, "run_menu", fake_run_menu)
    _main_menu(AppConfig())

    by_value = {item.value: item for item in captured["items"]}
    # v9.2.0: the session starts in the one agent; the project workspace is a
    # first-class menu item, and no mode item exists.
    assert by_value["chat"].shortcut == "1"
    assert by_value["memory"].shortcut == "2"
    assert by_value["settings"].shortcut == "3"
    assert by_value["exit"].shortcut == "4"
    assert "agent" not in by_value
    assert "codemode" not in by_value
    assert "assist" not in by_value
    # Start Coding Session stays reachable so the menu is never a dead end.
    assert "chat" in by_value
    assert "Seed Code v" in captured["kwargs"]["title"]


# --- menu -> router -> handler -> runtime state ------------------------------

def test_the_removed_mode_commands_are_reported_not_routed() -> None:
    """v9.2.0: the mode commands are gone; the router says so, cleanly."""
    ui = _StubUI()
    config = AppConfig()
    for command in ("/agent on", "/codemode on", "/chat on", "/mode agent"):
        _dispatch_command(ui, config, None, command)
    assert any("Unknown command" in message for message in ui.messages)


# --- command error UX --------------------------------------------------------

def test_network_errors_render_as_a_network_block_without_secrets() -> None:
    ui = _StubUI()
    request = httpx.Request(
        "GET", "https://x/", headers={"Authorization": "Bearer sk-super-secret"}
    )
    exc = httpx.HTTPStatusError(
        "e", request=request, response=httpx.Response(401, request=request)
    )

    _report_command_error(ui, exc)

    text = " ".join(ui.messages)
    assert "[Network Error]" in text
    assert "sk-super-secret" not in text


def test_unexpected_errors_keep_their_message() -> None:
    ui = _StubUI()
    _report_command_error(ui, RuntimeError("boom"))
    assert "[Command Error] boom" in " ".join(ui.messages)
