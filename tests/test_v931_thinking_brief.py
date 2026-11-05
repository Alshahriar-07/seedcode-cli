"""v9.3.1 thinking brief (v9.3.2: minimal presentation).

Locks in the thinking-panel contract:

* a model/provider that exposes **no** reasoning stream records the single
  truthful line ``Reasoning stream unavailable for this model.`` — never
  anything fabricated, never a fake progress sentence;
* the panel is MINIMAL: one small ``Thinking...`` label, then the model's own
  reasoning, both dim/faded — no large heading, no LIVE badge, no model
  or provider name, no execution-status metadata. Completed reasoning is
  preserved after the turn with the same minimal presentation;
* blank deltas never count as reasoning.
"""

from __future__ import annotations

from seedcode.ui.state import AppState
from seedcode.ui.tui import ChatTUI

_FALLBACK = "Reasoning stream unavailable for this model."


def _plain(fragments) -> str:
    """Flatten prompt_toolkit-style (style, text) fragments to text lines."""
    return "\n".join("".join(t for _style, t in line) for line in fragments)


def _tui() -> ChatTUI:
    return ChatTUI(AppState(), width=100, height=40)


# ---------------------------------------------------------------------------
# the honest fallback (no fabricated reasoning, ever)
# ---------------------------------------------------------------------------
def test_a_turn_without_reasoning_records_the_honest_fallback() -> None:
    state = AppState()
    state.reset_thinking()
    assert state.thinking_text == ""  # nothing fabricated while the model works
    state.collapse_thinking()  # turn end
    assert state.thinking_text == _FALLBACK


def test_a_real_reasoning_stream_is_never_replaced_by_the_fallback() -> None:
    state = AppState()
    state.reset_thinking()
    state.append_thinking("Inspecting the existing auth structure...\n")
    state.collapse_thinking()
    assert "Inspecting the existing auth structure" in state.thinking_text
    assert _FALLBACK not in state.thinking_text


def test_blank_deltas_never_count_as_reasoning() -> None:
    state = AppState()
    state.reset_thinking()
    state.append_thinking("")      # empty chunk must be ignored
    state.append_thinking("   \n")  # a whitespace chunk is not reasoning
    assert state.thinking_text == ""
    state.collapse_thinking()
    assert state.thinking_text == _FALLBACK


def test_the_fallback_record_is_idempotent() -> None:
    state = AppState()
    state.reset_thinking()
    state.collapse_thinking()
    state.collapse_thinking()  # second end-of-turn collapse (e.g. an error path)
    assert state.thinking_text.count(_FALLBACK) == 1


def test_finish_thinking_records_the_fallback_without_collapsing() -> None:
    # Plan sessions keep their panel across turns; the end-of-request record
    # marks the session honestly while leaving the panel where it was.
    state = AppState()
    state.reset_thinking()
    state.finish_thinking()
    assert state.thinking_text == _FALLBACK
    assert state.thinking_open is True
    assert state.reasoning_seen is False


def test_begin_reasoning_resets_the_window_without_touching_the_buffer() -> None:
    state = AppState()
    state.reset_thinking()
    state.append_thinking("session-spanning reasoning\n")
    state.begin_reasoning()
    assert "session-spanning reasoning" in state.thinking_text  # history kept
    state.finish_thinking()  # this request streamed no reasoning
    assert state.thinking_text.count("session-spanning reasoning") == 1
    assert _FALLBACK not in state.thinking_text


def test_completed_thinking_is_preserved_after_the_turn() -> None:
    state = AppState()
    state.reset_thinking()
    state.append_thinking("Reusing the existing user model.\n")
    state.collapse_thinking()
    state.toggle_thinking()  # Ctrl+B re-expands the completed panel
    assert "Reusing the existing user model." in state.thinking_text


# ---------------------------------------------------------------------------
# minimal presentation: label + content only, never metadata
# ---------------------------------------------------------------------------
def test_the_panel_shows_the_models_real_reasoning_only() -> None:
    """The panel renders the model's own stream, never a status message."""
    tui = _tui()
    tui.state.reset_thinking()
    tui.state.set_briefing(
        active=True, status="Working", activity="Reading project files"
    )
    tui.state.add_activity("action", "Reading project files")
    tui.state.append_thinking("The user wants the login page fixed.\nI should read the form first.")
    text = _plain(tui._thinking_fragments(40, tui._thinking_height()))
    assert "Thinking" in text
    assert "The user wants the login page fixed." in text
    assert "I should read the form first." in text
    # Tool activity is not reasoning and must stay out of this section.
    assert "Reading project files" not in text


def test_the_panel_never_carries_headings_badges_or_model_metadata() -> None:
    tui = _tui()
    tui.state.reset_thinking()
    tui.state.update(provider="OpenRouter", model="DeepSeek V4.1 Flash")
    tui.state.set_briefing(
        active=True,
        status="Executing",
        task="Task 1/3 Create login page",
        activity="Editing tui.py",
    )
    tui.state.add_activity("action", "Reading source files")
    tui.state.append_thinking("The project uses Python and TypeScript.\nNow I will check the composer.")
    text = _plain(tui._thinking_fragments(80, tui._thinking_height()))
    assert "LIVE" not in text
    assert "AI THINKING" not in text
    assert "BRIEFING" not in text
    assert "DeepSeek" not in text and "OpenRouter" not in text
    assert "Status:" not in text and "Current task" not in text
    assert "The project uses Python and TypeScript." in text
    assert "Reading source files" not in text


def test_completed_reasoning_remains_available_after_the_turn() -> None:
    tui = _tui()
    tui.state.reset_thinking()
    tui.state.append_thinking("I verified the existing login component; it needs a label.")
    tui.state.set_briefing(active=True, status="Working")
    tui.state.reset_briefing()  # the turn is over
    text = _plain(tui._thinking_fragments(80, tui._thinking_height()))
    assert "Thinking" in text
    assert "LIVE" not in text
    assert "it needs a label" in text


# ---------------------------------------------------------------------------
# presentation: dim/faded and visually quieter than normal responses
# ---------------------------------------------------------------------------
def test_reasoning_uses_muted_secondary_text() -> None:
    tui = _tui()
    tui.state.reset_thinking()
    tui.state.append_thinking("Read the config, then the composer.")
    from seedcode.ui.theme import active_palette

    dim_style = f"fg:{active_palette().dim}"
    fragments = tui._thinking_fragments(40, tui._thinking_height())
    assert any(style == dim_style for styles in fragments for style, _t in styles)


def test_no_activity_is_fabricated_when_idle() -> None:
    tui = _tui()
    tui.state.reset_thinking()
    assert tui._thinking_height() == 0
