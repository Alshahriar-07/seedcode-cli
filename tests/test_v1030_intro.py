"""v10.3.0 interface redesign: intro state, composer hierarchy, live strip.

The redesign is presentation-only: every value asserted here comes from the
real :class:`AppState` (or is the single static tip), and the live thinking
strip still shows only what the backend published.
"""

from __future__ import annotations

import re

from seedcode.ui.intro import (
    BLOCK_MIN_ROWS,
    BLOCK_MIN_WIDTH,
    COMPACT_WORDMARK,
    intro_lines,
    intro_text,
    tip_for,
)
from seedcode.ui.state import AppState
from seedcode.ui.tui import ChatTUI
from seedcode.utils.text import safe_text


def _plain(lines) -> str:
    return "\n".join("".join(part for _style, part in line) for line in lines)


# --- intro / empty state ------------------------------------------------------
def test_intro_is_centred_and_keeps_only_the_essentials() -> None:
    state = AppState(workspace="C:/Users/example/project")
    text = intro_text(state, 80, 24)
    body = text.splitlines()

    assert "Ask anything" in text
    assert tip_for(False) in text
    assert "/connect" in text
    # Quiet: the intro never repeats workspace or configuration detail.
    assert "Workspace" not in text
    assert "C:/Users" not in text
    # The block wordmark is used when it fits, and every line is width-safe.
    assert "\u2588" in text
    assert all(len(line) <= 80 for line in body)
    # Centred: every art row is the same width and none touches an edge.
    art = [line for line in body if "\u2588" in line]
    assert len({len(line) for line in art}) == 1
    first = art[0]
    assert len(first) - len(first.lstrip(" ")) > 0  # indented from the left
    assert len(first) < 80  # and never fills the terminal edge to edge


def test_intro_falls_back_to_the_compact_wordmark_when_narrow() -> None:
    text = intro_text(AppState(), BLOCK_MIN_WIDTH - 2, 24)
    assert COMPACT_WORDMARK in text
    assert "\u2588" not in text
    assert all(len(line) <= BLOCK_MIN_WIDTH - 2 for line in text.splitlines())


def test_intro_trims_cleanly_in_a_short_terminal() -> None:
    lines = intro_lines(AppState(), 60, BLOCK_MIN_ROWS - 6)
    assert 0 < len(lines) <= BLOCK_MIN_ROWS - 6
    assert COMPACT_WORDMARK in _plain(lines)


def test_configured_intro_shows_the_model_and_no_onboarding_tip() -> None:
    state = AppState(provider="OpenRouter", model="DeepSeek V4.1 Flash Free")
    text = intro_text(state, 80, 24)
    assert "DeepSeek V4.1 Flash Free" in text
    assert "OpenRouter" in text
    assert "/connect" not in text
    assert tip_for(True) in text


def test_tui_shows_the_intro_only_until_the_first_message() -> None:
    tui = ChatTUI(AppState(), width=80, height=24)
    assert _plain(tui._slice(80, 20)).strip() == ""  # no intro before startup

    tui.banner_text()
    assert "Ask anything" in _plain(tui._slice(80, 20))

    tui.append_user("hello")
    assert "Ask anything" not in _plain(tui._slice(80, 20))
    assert "You > hello" in _plain(tui._slice(80, 20))


# --- composer -----------------------------------------------------------------
def test_composer_metadata_adds_the_reasoning_level_last() -> None:
    state = AppState(
        agent_state="Build",
        model="DeepSeek V4.1 Flash Free",
        provider="OpenRouter",
        reasoning="high",
    )
    tui = ChatTUI(state, width=100, height=24)
    metadata = "".join(text for _style, text in tui._composer_metadata())
    assert metadata.startswith("Build")
    assert metadata.index("Build") < metadata.index("DeepSeek")
    assert metadata.index("DeepSeek") < metadata.index("OpenRouter") < metadata.index("high")


def test_composer_metadata_omits_missing_values_and_truncates() -> None:
    state = AppState(agent_state="Build", model="x" * 80, provider="OpenRouter")
    tui = ChatTUI(state, width=32, height=24)
    metadata = "".join(text for _style, text in tui._composer_metadata())
    assert len(metadata) <= 32 - 6
    assert "Build" in metadata
    # No invented reasoning level when the configuration has none.
    assert "high" not in metadata and "medium" not in metadata


# --- live thinking strip ------------------------------------------------------
def test_thinking_strip_has_a_heading_elapsed_time_and_left_rail() -> None:
    tui = ChatTUI(AppState(), width=80, height=24)
    tui.state.set_briefing(active=True, status="Working", activity="Reading files")
    tui.state.add_activity("action", "Inspecting the workspace")
    tui.state.append_thinking("The user wants the intro polished.")
    tui.start_thinking("Working")
    try:
        text = _plain(tui._thinking_fragments(80, tui._thinking_height()))
    finally:
        tui.stop_thinking()
    assert "Thinking" in text
    assert re.search(r"\u00b7 \d+\.\ds", text)
    assert "\u2503" in text  # the thin blue reasoning rail
    assert "The user wants the intro polished." in text
    # Activity text never leaks into the reasoning section.
    assert "Inspecting the workspace" not in text


def test_note_activity_uses_the_quiet_information_mark() -> None:
    tui = ChatTUI(AppState(), width=80, height=24)
    tui.append_activity("note", "Analyzing the task...")
    content = safe_text(_plain(tui.buffer.lines()))
    assert "\u2139 Analyzing the task..." in content
    assert "INFO:" not in content
    assert "AI >" not in content


# --- startup ------------------------------------------------------------------
def test_unconfigured_turn_points_at_connect_instead_of_failing() -> None:
    """First run: no wizard, no traceback — one clear instruction."""
    from types import SimpleNamespace

    from seedcode.app import _TuiController
    from seedcode.core.models import AppConfig

    lines: list[str] = []

    class _UI:
        def warning(self, text: str) -> None:
            lines.append(text)

        def dim(self, text: str) -> None:
            lines.append(text)

    controller = _TuiController.__new__(_TuiController)
    controller.config = AppConfig()  # nothing configured
    controller.ui = _UI()
    controller.agent = None
    controller.tui = SimpleNamespace(sync_from_config=lambda _config: None)

    controller.submit("build me an app")

    assert any("No AI provider" in line for line in lines)
    assert any("/connect" in line for line in lines)
    assert controller.agent is None  # no engine was built for a dead turn


def test_connect_is_an_alias_of_the_existing_provider_flow() -> None:
    import seedcode.commands as commands

    assert commands._ALIASES.get("connect") == "provider"
    assert "provider" in commands._REGISTRY
