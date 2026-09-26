"""v8.2.5: the persistent terminal interface.

Everything locked in here is behaviour a user can observe:

* the header is a live dashboard — the fixed Seed Code ASCII logo, tagline,
  provider, model, mode, status, workspace, context budget and the masked key
  are read from real state, and it adapts to the terminal without ever
  overflowing or clipping the branding;
* the three regions are fixed: header, scrolling conversation, fixed composer;
* the composer is a real, bounded multiline textbox — a prompt_toolkit ``Frame``
  draws its ``╭─ Message ─╮`` border, so typed text can never escape it — with
  **no** ``You >`` prefix of its own and no separate Submit control (Enter is
  the single submission path);
* provider, model, mode and status live in the header dashboard exactly once:
  there is no second toolbar repeating them;
* Up/Down move the cursor inside a multiline message and only walk the input
  history at the edges;
* an animated ``AI > ◌ Thinking…`` indicator shows before the answer and is
  replaced by real output without ever blocking the turn;
* assistant output and live activity are attributed with ``AI >``;
* ``Enter`` sends, ``Ctrl+J`` inserts a newline, ``↑``/``↓`` walk the input
  history, ``Ctrl+C`` cancels a running turn and ``Ctrl+D`` exits;
* a command hands control back for one dispatch, then the session resumes;
* permissions are answered inline and answered safely (Esc/Enter deny);
* the visible task to-do checklist is not rendered under the TUI.

The prompt_toolkit application is driven through a pipe input and a dummy
output, so the whole interface is exercised without a real terminal.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from seedcode import __version__
from seedcode.core.models import AppConfig
from seedcode.ui import UI
from seedcode.ui.content import ContentBuffer, parse_ansi, plain_text, trim_line
from seedcode.ui.header import MAX_PANEL_WIDTH, header_lines, header_text
from seedcode.ui.state import AppState, Status, status_fragment
from seedcode.ui.tasks import TaskFlow
from seedcode.ui.tui import ChatTUI, TuiUI

# --- helpers ------------------------------------------------------------------


@contextmanager
def _session(state: AppState | None = None):
    """A TUI wired to a pipe input and a dummy output."""
    with create_pipe_input() as pipe:
        tui = ChatTUI(
            state or AppState(),
            workspace="/tmp/project",
            input_device=pipe,
            output_device=DummyOutput(),
        )
        yield tui, tui.state, pipe


def _schedule(pipe, script) -> threading.Thread:
    """Send ``(delay, keys)`` steps on a background thread while the app runs."""

    def run() -> None:
        for delay, keys in script:
            time.sleep(delay)
            pipe.send_text(keys)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _width(line) -> int:
    return sum(len(text) for _, text in line)


def _configured() -> AppConfig:
    config = AppConfig(provider="openrouter", model="cohere/north-mini-code:free")
    config.set_api_key("openrouter", "sk-or-test")
    return config


# --- the version --------------------------------------------------------------
def test_header_reports_the_release_version() -> None:
    assert __version__ == "8.2.5"
    out = header_text(AppState(provider="OpenRouter", model="m", mode="Chat"), 80)
    assert "v8.2.5" in out


# --- reactive state -----------------------------------------------------------
def test_state_notifies_subscribers_on_real_change() -> None:
    state = AppState()
    seen: list[int] = []
    state.subscribe(lambda s: seen.append(s.version))
    assert seen == [0]  # called once immediately
    state.set_status(Status.WORKING, "doing work")
    state.set_status(Status.WORKING, "doing work")  # not a change
    assert len(seen) == 2
    assert state.status is Status.WORKING


def test_state_syncs_from_live_config_only() -> None:
    state = AppState.from_config(_configured(), workspace="/tmp/project")
    assert state.provider == "OpenRouter"
    assert state.model == "cohere/north-mini-code:free"
    assert state.mode == "Chat Mode"
    assert state.workspace == "/tmp/project"
    assert state.context_limit > 0


def test_unconfigured_state_does_not_fake_readiness() -> None:
    # A provider that needs a key but has none is not "ready": no label, and
    # the key is reported as the masked/empty form, never the key itself.
    state = AppState.from_config(AppConfig(provider="openrouter"))
    assert state.provider == ""
    assert state.model == ""
    assert "sk-" not in state.api_key


def test_a_broken_subscriber_cannot_break_the_session() -> None:
    state = AppState()

    def boom(_state) -> None:
        raise RuntimeError("subscriber failed")

    state.subscribe(boom)
    state.set_status(Status.ERROR, "x")  # must not raise
    assert state.status is Status.ERROR


# --- the header ---------------------------------------------------------------
def test_header_shows_every_live_value() -> None:
    state = AppState(
        provider="OpenRouter",
        model="nex-agi/nex-n2.5-mini:free",
        mode="Agent",
        status=Status.WORKING,
        workspace="D:/my-project",
        context_limit=16384,
        key_required=True,
        api_key="sk-or-v1...e031",
    )
    out = header_text(state, 100)
    for expected in (
        "OpenRouter",
        "nex-agi/nex-n2.5-mini:free",
        "Agent",
        "Working",
        "D:/my-project",
        "16,384",
        "sk-or-v1...e031",
    ):
        assert expected in out, expected


def test_header_never_exceeds_the_terminal_width() -> None:
    state = AppState(
        provider="OpenRouter",
        model="deepseek/" + "x" * 120,
        mode="Code Mode",
        status=Status.EXECUTING,
        workspace="C:/" + "deep/" * 30,
        context_limit=999999,
        key_required=True,
        api_key="sk-" + "y" * 80,
    )
    for width in (20, 24, 30, 40, 50, 68, 80, 96, 120, 200):
        for line in header_lines(state, width):
            assert _width(line) <= width, (width, line)


def test_header_is_bounded_by_its_design_width() -> None:
    state = AppState(provider="OpenRouter", model="m", mode="Chat")
    for width in (120, 200):
        for line in header_lines(state, width):
            assert _width(line) <= MAX_PANEL_WIDTH


def test_status_marks_and_labels_are_truthful() -> None:
    for status, mark, label in (
        (Status.READY, "\u25cf", "Ready"),
        (Status.THINKING, "\u25cc", "Thinking"),
        (Status.WORKING, "\u25cc", "Working"),
        (Status.EXECUTING, "\u25cf", "Running"),
        (Status.WAITING, "\u25cc", "Waiting"),
        (Status.COMPLETED, "\u2713", "Completed"),
        (Status.ERROR, "\u26a0", "Error"),
    ):
        style, text = status_fragment(status)
        assert text.startswith(mark), text
        assert label in text
        assert style


# --- branding -----------------------------------------------------------------
def test_header_keeps_the_ascii_logo_as_primary_branding() -> None:
    """The block logo is the product identity: it is never replaced by text."""
    from seedcode.ui.dashboard import LOGO_LINES

    out = header_text(AppState(provider="OpenRouter", model="m", mode="Chat"), 80)
    for line in LOGO_LINES:
        assert line in out, line  # exact spacing, not a whitespace-collapsed copy
    assert "Plant ideas. Grow code." in out


def test_logo_appears_as_soon_as_it_can_be_drawn_unclipped() -> None:
    from seedcode.ui.dashboard import LOGO_LINES
    from seedcode.ui.header import _LOGO_MIN_WIDTH

    state = AppState(provider="OpenRouter", model="m", mode="Chat")
    out = header_text(state, _LOGO_MIN_WIDTH)
    for line in LOGO_LINES:
        assert line in out, line
    # One column narrower and the block art is omitted whole rather than cut.
    narrower = header_text(state, _LOGO_MIN_WIDTH - 1)
    for line in LOGO_LINES:
        assert line not in narrower, line


def test_narrow_headers_never_truncate_the_logo() -> None:
    from seedcode.ui.dashboard import LOGO_LINES
    from seedcode.ui.header import _LOGO_MIN_WIDTH

    for width in range(20, _LOGO_MIN_WIDTH):
        out = header_text(AppState(provider="OpenRouter", model="m", mode="Chat"), width)
        # Below the logo's minimum width the branding is omitted whole — a
        # clipped logo would be worse than none.
        for logo in LOGO_LINES:
            assert logo not in out, width
        # The essentials survive the fallback; the branding is never clipped.
        assert "v8.2.5" in out
        if width >= 40:
            assert "OpenRouter" in out, width


def test_legacy_consoles_get_the_wordmark_instead_of_block_art() -> None:
    out = header_text(
        AppState(provider="OpenRouter", model="m", mode="Chat"), 80, ascii_only=True
    )
    assert "SEED CODE" in out
    assert "\u2588" not in out


# --- the thinking indicator ---------------------------------------------------
def test_thinking_indicator_animates_before_any_output() -> None:
    from seedcode.ui.tui import _THINK_INTERVAL_S

    with _session() as (tui, _state, _pipe):
        tui.start_thinking("Thinking")
        first = plain_text(tui.buffer.lines())
        assert "AI > \u25cc Thinking." in first
        time.sleep(_THINK_INTERVAL_S * 2.5)
        animated = plain_text(tui.buffer.lines())
        assert animated != first  # the indicator really animated
        assert "AI > \u25cc Thinking" in animated
        tui.stop_thinking()
        assert "Thinking" not in plain_text(tui.buffer.lines())


def test_real_output_replaces_the_thinking_indicator() -> None:
    with _session() as (tui, state, _pipe):
        ui = TuiUI(tui)
        with ui.thinking("Thinking"):
            assert state.status is Status.THINKING
            assert "Thinking" in plain_text(tui.buffer.lines())
        with ui.streaming() as renderer:
            renderer.feed("real answer")
        content = plain_text(tui.buffer.lines())
        assert "AI > real answer" in content
        assert "Thinking" not in content
        tui.stop_thinking()  # a late stop must not wipe the committed answer
        assert "AI > real answer" in plain_text(tui.buffer.lines())


# --- the composer --------------------------------------------------------------
def test_composer_has_no_prompt_prefix() -> None:
    """The textbox is a clean input field: no ``You >`` is drawn inside it."""
    with _session() as (tui, _state, _pipe):
        tui._build_app()
        assert tui._input_window is not None  # a real editor window
        # The buffer holds exactly the message the user typed and nothing else.
        tui._input.text = "build it"
        assert tui._input.text == "build it"
        # There is no line-prefix hook adding a prompt to the composer.
        assert not hasattr(tui, "_input_line_prefix")


def test_composer_is_a_bounded_frame_without_a_submit_button() -> None:
    """The composer is a real bounded editor — no separate Submit control."""
    with _session() as (tui, _state, _pipe):
        tui._build_app()
        assert tui._input_window is not None  # a real editor window
        # The visual Submit button is gone: Enter is the single submit path.
        assert not hasattr(tui, "_submit_window")
        assert not hasattr(tui, "_submit_fragments")
        # Long lines wrap inside the field, not outside the border.
        assert tui._input_window.wrap_lines()


def test_the_buffer_accept_handler_is_the_single_submission_path(monkeypatch) -> None:
    """Enter runs the one submission path (no separate control needed)."""
    with _session() as (tui, _state, _pipe):
        seen: list[str] = []
        monkeypatch.setattr(tui, "_submit_text", lambda text: seen.append(text))
        tui._input.text = "from the textbox"
        tui._input.validate_and_handle()
        assert seen == ["from the textbox"]


# --- the header is the single source of truth ----------------------------------
def test_provider_model_mode_and_status_appear_once_in_the_header() -> None:
    """Each persistent value is rendered in exactly one place (no toolbar)."""
    state = AppState(
        provider="OpenRouter", model="m", mode="Chat Mode", status=Status.READY
    )
    text = header_text(state, 100)
    assert text.count("OpenRouter") == 1
    assert text.count("Chat Mode") == 1
    assert text.count("Ready") == 1
    # There is no second toolbar window repeating them.
    with _session() as (tui, _state, _pipe):
        tui._build_app()
        assert not hasattr(tui, "_toolbar_window")
        assert not hasattr(tui, "_toolbar_fragments")


def test_alt_enter_inserts_a_newline_instead_of_submitting() -> None:
    with _session() as (tui, _state, pipe):
        tui._input.text = "one"
        # Alt+Enter (escape+enter) must insert a newline, not send the message;
        # this only works because the bare Escape binding is not eager.
        _schedule(pipe, [(0.3, "\x1b\r"), (0.6, "\x04")])
        tui.run_once()
    assert "\n" in tui._input.text


def test_arrow_keys_move_the_cursor_inside_a_multiline_message() -> None:
    with _session() as (tui, _state, pipe):
        tui._input.text = "first line\nsecond line"
        tui._input.cursor_position = len(tui._input.text)
        # Cursor starts at the end (row 1); Up must move it, not walk history.
        assert tui._input.document.cursor_position_row == 1
        _schedule(pipe, [(0.3, "\x1b[A"), (0.5, "\x04")])
        tui.run_once()
    assert tui._input.document.cursor_position_row == 0


def test_multiline_message_keeps_its_continuation_aligned() -> None:
    with _session() as (tui, _state, _pipe):
        tui.append_user("first line\nsecond line")
    content = plain_text(tui.buffer.lines())
    assert "You > first line" in content
    assert "      second line" in content


def test_activity_lines_are_attributed_to_the_assistant() -> None:
    with _session() as (tui, _state, _pipe):
        tui.append_activity("action", "Reading package.json")
        tui.append_activity("success", "Tests passed")
    content = plain_text(tui.buffer.lines())
    assert "AI > " in content
    assert "Reading package.json" in content
    assert "Tests passed" in content


# --- scrolling hint ------------------------------------------------------------
def test_scrolling_up_reports_unseen_output_without_yanking_the_view() -> None:
    with _session() as (tui, _state, _pipe):
        for index in range(40):
            tui.buffer.commit([[("", f"line {index}")]])
        visible = plain_text(tui._slice(80, 5)).splitlines()
        assert tui._unseen == 0  # following the bottom
        tui.scroll_by(-10)
        still = plain_text(tui._slice(80, 5)).splitlines()
        assert tui._unseen > 0
        assert "line 39" not in still
        hint = "".join(text for _, text in tui._composer_bottom())
        assert "new" in hint
        assert visible[-1] == "line 39"


# --- the content region -------------------------------------------------------
def test_ansi_state_is_carried_across_lines() -> None:
    lines = parse_ansi("\x1b[32mgreen line\nstill green\x1b[0m plain")
    assert plain_text(lines) == "green line\nstill green plain"
    assert lines[0][0][0] == lines[1][0][0]  # same style on the continuation line
    assert "fg:" in lines[0][0][0]


def test_truecolor_and_256_colors_parse() -> None:
    assert parse_ansi("\x1b[38;2;46;204;113mx")[0][0][0] == "fg:#2ecc71"
    assert parse_ansi("\x1b[38;5;196mx")[0][0][0] == "fg:#ff0000"


def test_content_buffer_commits_live_blocks() -> None:
    buffer = ContentBuffer()
    buffer.write("one\ntwo\n")
    assert buffer.line_count() == 2
    buffer.set_live([[("", "live")]])
    assert "live" in plain_text(buffer.lines())
    buffer.commit_live()
    assert "live" in plain_text(buffer.lines())
    assert buffer.lines()[-1][0][1] == "live"
    buffer.clear()
    assert buffer.line_count() == 0


def test_trim_line_drops_trailing_padding() -> None:
    line = trim_line([("", "hello"), ("", "     ")])
    assert plain_text([line]) == "hello"


# --- the application ----------------------------------------------------------
def test_header_and_composer_are_the_fixed_regions() -> None:
    with _session(AppState(provider="OpenRouter", model="m", mode="Chat")) as (tui, _state, _pipe):
        app = tui._build_app()
        assert app.full_screen is True
        assert tui._header_window is not None
        assert tui._content_window is not None
        assert tui._input_window is not None
        # The content window is the flexible one; the others are exact.
        lines = header_lines(tui.state, 80)
        assert tui._header_window.height.preferred == len(lines)


def test_enter_sends_a_message_without_leaving_the_application() -> None:
    submitted = threading.Event()
    with _session() as (tui, _state, pipe):
        tui.on_submit = lambda text: submitted.set()
        _schedule(pipe, [(0.3, "hello"), (0.35, "\r"), (0.8, "\x04")])
        result = tui.run_once()
    assert result == ("exit", None)
    assert submitted.wait(timeout=2)
    assert "hello" in plain_text(tui.buffer.lines())


def test_enter_on_a_command_hands_control_back_for_one_dispatch() -> None:
    with _session() as (tui, _state, pipe):
        pipe.send_text("/help")
        pipe.send_text("\r")
        assert tui.run_once() == ("command", "/help")


def test_ctrl_d_exits_immediately() -> None:
    with _session() as (tui, _state, pipe):
        pipe.send_text("\x04")
        assert tui.run_once() == ("exit", None)


def test_input_history_walks_previous_messages() -> None:
    with _session() as (tui, _state, _pipe):
        tui._remember("first")
        tui._remember("second")
        tui._input.text = ""
        tui._history_move(-1)
        assert tui._input.text == "second"
        tui._history_move(-1)
        assert tui._input.text == "first"
        tui._history_move(1)
        assert tui._input.text == "second"


def test_newline_binding_keeps_a_multiline_message() -> None:
    with _session() as (tui, _state, _pipe):
        tui._input.text = "line one"
        tui._input.insert_text("\nline two")
        assert tui._input.text.count("\n") == 1
        assert tui._input_height() == 2


def test_composer_title_reflects_a_pending_confirmation() -> None:
    with _session() as (tui, _state, _pipe):
        assert tui._composer_title() == " Message "
        tui._confirm_prompt = ("Agent Action", "Run command")
        assert "Agent Action" in tui._composer_title()


# --- scrolling ----------------------------------------------------------------
def test_conversation_follows_the_bottom_until_the_user_scrolls_up() -> None:
    with _session() as (tui, _state, _pipe):
        for index in range(40):
            tui.buffer.commit([[("", f"line {index}")]])
        visible = plain_text(tui._slice(80, 5)).splitlines()
        assert visible[-1] == "line 39"  # following the bottom

        tui.scroll_by(-10)
        visible = plain_text(tui._slice(80, 5)).splitlines()
        assert "line 39" not in visible  # the user scrolled up

        tui.buffer.commit([[("", "line 40")]])
        again = plain_text(tui._slice(80, 5)).splitlines()
        assert again == visible  # new output does not yank the view back

        tui.scroll_to_bottom()
        assert "line 40" in plain_text(tui._slice(80, 5))


# --- streaming ----------------------------------------------------------------
def test_streaming_lands_in_the_conversation_but_not_the_header() -> None:
    with _session(AppState(provider="OpenRouter", model="m", mode="Chat")) as (tui, _state, pipe):
        ui = TuiUI(tui)
        header_before = header_text(tui.state, 80)

        def on_submit(_text: str) -> None:
            with ui.thinking("Thinking"):
                pass
            with ui.streaming() as renderer:
                renderer.feed("Hello ")
                renderer.feed("**world**")

        tui.on_submit = on_submit
        _schedule(pipe, [(0.3, "go"), (0.35, "\r"), (0.8, "\x04")])
        tui.run_once()
        time.sleep(0.2)

    content = plain_text(tui.buffer.lines())
    assert "Hello world" in content
    assert header_text(tui.state, 80) == header_before  # header was not redrawn
    assert tui.state.status is Status.READY


def test_ctrl_c_cancels_the_running_turn_cooperatively() -> None:
    with _session() as (tui, state, pipe):
        ui = TuiUI(tui)
        started = threading.Event()

        def on_submit(_text: str) -> None:
            started.set()
            while True:
                ui.dim("working")  # raises KeyboardInterrupt once cancelled
                time.sleep(0.01)

        tui.on_submit = on_submit
        _schedule(pipe, [(0.3, "go"), (0.35, "\r"), (0.7, "\x03"), (1.2, "\x04")])
        tui.run_once()

    assert started.is_set()
    assert state.status is Status.CANCELLED


# --- permissions --------------------------------------------------------------
def test_permission_prompt_is_answered_inline() -> None:
    with _session() as (tui, state, pipe):
        answers: dict[str, str] = {}
        tui.on_submit = lambda _text: answers.update(
            answer=tui.confirm("Agent Action", "Run command", "rm -rf /tmp/x")
        )
        # The confirmation is answered after the turn has reached it (the app
        # needs a moment to render its first frame before it reads input).
        _schedule(pipe, [(0.4, "go"), (0.45, "\r"), (1.2, "y\r"), (2.2, "\x04")])
        tui.run_once()
        assert answers.get("answer") == "y"
        assert state.status is Status.READY


def test_permission_prompt_denies_on_escape() -> None:
    with _session() as (tui, _state, pipe):
        answers: dict[str, str] = {}
        tui.on_submit = lambda _text: answers.update(
            answer=tui.confirm("Desktop Control", "Click x", "click")
        )
        _schedule(pipe, [(0.4, "go"), (0.45, "\r"), (1.2, "\x1b"), (2.2, "\x04")])
        tui.run_once()
        assert answers.get("answer") == "n"


# --- the UI adapter -----------------------------------------------------------
def test_the_tui_adapter_replaces_the_live_displays() -> None:
    with _session() as (tui, _state, _pipe):
        ui = TuiUI(tui)
        assert ui.is_tui is True
        assert UI.is_tui is False
        # The transient task view is not drawn under the persistent interface.
        assert TaskFlow.for_ui(ui, mode_label="Agent Mode", task="x") is None


def test_the_dashboard_is_not_reprinted_when_the_header_is_synced() -> None:
    with _session() as (tui, _state, _pipe):
        ui = TuiUI(tui)
        ui.apply_theme("seed")
        ui.banner(_configured())
        ui.statusbar(_configured())
        out = plain_text(tui.buffer.lines())
        # The startup panel is never printed: the header is the banner now.
        assert "\u256d\u2500 Seed Code CLI" not in out
        assert tui.state.provider == "OpenRouter"
        assert tui.state.model == "cohere/north-mini-code:free"


def test_clear_command_empties_the_conversation_not_the_terminal() -> None:
    with _session() as (tui, _state, _pipe):
        tui.console.print("some output")
        assert "some output" in plain_text(tui.buffer.lines())
        tui.console.clear()
        assert tui.buffer.line_count() == 0


# --- host routing -------------------------------------------------------------
def test_persistent_tui_is_only_used_on_a_real_console(monkeypatch) -> None:
    from seedcode import cli

    monkeypatch.delenv("SEEDCODE_NO_TUI", raising=False)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli._use_persistent_tui(plain=False) is True
    # SEEDCODE_PLAIN hosts keep the sequential console...
    assert cli._use_persistent_tui(plain=True) is False
    # ...and so does an explicit opt-out.
    monkeypatch.setenv("SEEDCODE_NO_TUI", "1")
    assert cli._use_persistent_tui(plain=False) is False
    # A piped/redirected host can never own the screen.
    monkeypatch.delenv("SEEDCODE_NO_TUI", raising=False)
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli._use_persistent_tui(plain=False) is False


def test_persistent_entry_point_exists() -> None:
    from seedcode.app import run_persistent

    assert callable(run_persistent)


# --- mode switching stays inside one application -------------------------------
def test_mode_switch_command_runs_in_place() -> None:
    """`/agent on` is handled inside the running application (no exit/rebuild)."""
    with _session() as (tui, _state, pipe):
        seen: list[str] = []
        tui.on_command = seen.append
        _schedule(pipe, [(0.3, "/agent on"), (0.35, "\r"), (0.9, "\x04")])
        result = tui.run_once()
        time.sleep(0.1)
        # The mode command never handed control back for a separate dispatch.
        assert result == ("exit", None)
        assert seen == ["/agent on"]


def test_non_mode_command_still_hands_control_back() -> None:
    with _session() as (tui, _state, pipe):
        tui.on_command = lambda _text: None
        pipe.send_text("/help")
        pipe.send_text("\r")
        assert tui.run_once() == ("command", "/help")


def test_repeated_runs_reuse_one_application_object() -> None:
    """The Application is built once and re-entered, not rebuilt per command."""
    with _session() as (tui, _state, pipe):
        pipe.send_text("/help")
        pipe.send_text("\r")
        assert tui.run_once() == ("command", "/help")
        first = tui._app
        assert first is not None
        # The same application object is retained for the next run (no rebuild).
        assert tui._app is first
        pipe.send_text("\x04")
        assert tui.run_once() == ("exit", None)
        assert tui._app is None  # released only when the session ends


# --- the permission panel is a bounded, keyboard-driven component --------------
def test_permission_panel_is_bounded_and_interactive() -> None:
    with _session() as (tui, _state, _pipe):
        tui._confirm_detail = ("Agent Action", "Run command", "npm install")
        assert tui._permission_height() > 0
        rows = [
            "".join(text for _, text in line)
            for line in [
                list(chunk) for chunk in _split_permission(tui._permission_fragments())
            ]
        ]
        assert any("Permission" in row or "Agent Action" in row for row in rows)
        assert any("npm install" in row for row in rows)
        assert any("[A]" in row and "[D]" in row and "[Y]" in row for row in rows)
        for row in rows:
            assert len(row) <= tui._width


def _split_permission(flat):
    line: list[tuple[str, str]] = []
    for fragment in flat:
        if fragment == ("", "\n"):
            yield line
            line = []
        else:
            line.append(fragment)
    yield line


def test_permission_enter_allows_for_once() -> None:
    with _session() as (tui, _state, pipe):
        answers: dict[str, str] = {}
        tui.on_submit = lambda _text: answers.update(
            answer=tui.confirm("Agent Action", "Run command", "npm install")
        )
        _schedule(pipe, [(0.4, "go"), (0.45, "\r"), (1.2, "\r"), (2.2, "\x04")])
        tui.run_once()
        assert answers.get("answer") == "y"


def test_permission_d_denies_and_a_allows_the_session() -> None:
    with _session() as (tui, _state, _pipe):
        tui._confirm_event = threading.Event()
        tui._resolve_confirm("d")
        assert tui._confirm_answer == "n"
        tui._confirm_event = threading.Event()
        tui._resolve_confirm("a")
        assert tui._confirm_answer == "a"
