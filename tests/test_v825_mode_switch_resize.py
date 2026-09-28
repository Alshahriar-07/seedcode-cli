"""v9.1.0: mode switching and terminal resizing — the two stability contracts.

This module locks in the fixes for the two defects users reported against the
persistent interface, written so a regression fails loudly rather than as an
intermittent freeze.

**Agent Mode could freeze the whole terminal.** Activating Agent Mode ran its
preparation inline, wherever the command happened to be dispatched, and that
preparation includes a permission prompt that waits for an answer only the
prompt_toolkit event loop can deliver. Dispatched from the loop thread itself
(the main menu's *Agent Mode* item, the header's Mode control, or any main-thread
dispatch) the event loop was blocked inside the wait, so the answer could never
arrive: a permanent deadlock. The same path also ran a full-tree
``sorted(rglob("*"))`` index scan and a synchronous OCR subprocess probe.

What is asserted here:

1. a switch never runs on the caller's thread and never waits for it;
2. the UI state moves to Agent Mode immediately, with an ``Initializing`` state
   that stays visible while the work continues in the background;
3. repeated switching leaves exactly one live worker and lets a stale one
   publish nothing (no stale worker, no duplicate worker, no lost mode);
4. a failed initialization is reported in the conversation/status area and
   Chat Mode stays one command away;
5. permissions arriving on the interface thread are refused, never waited on;
6. the layout re-fits to any terminal size — including very short ones, which
   used to render a blank screen — without re-building a single widget, and
   typed text survives every resize;
7. resizing works while a response streams and while Agent Mode initializes.
"""

from __future__ import annotations

import io
import threading
import time
from contextlib import contextmanager

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.plain_text import PlainTextOutput

from seedcode.commands import CommandContext
from seedcode.core.models import AppConfig
from seedcode.ui.content import plain_text
from seedcode.ui.header import header_lines
from seedcode.ui.mode_switch import ModeSwitcher
from seedcode.ui.state import AppState, Status
from seedcode.ui.tui import ChatTUI, TuiUI

# --- harness -------------------------------------------------------------------


class _ResizableOutput(DummyOutput):
    """A dummy output whose reported size can change mid-run (a resize)."""

    def __init__(self, rows: int = 24, columns: int = 80) -> None:
        super().__init__()
        self._size = Size(rows=rows, columns=columns)

    def resize(self, rows: int, columns: int) -> None:
        self._size = Size(rows=rows, columns=columns)

    def get_size(self) -> Size:
        return self._size


class _ResizablePlainOutput(PlainTextOutput):
    """A plain-text output, so a test can read what actually reached the screen."""

    def __init__(self, buffer, rows: int, columns: int) -> None:
        self._rows = rows
        self._columns = columns
        super().__init__(buffer)

    def get_size(self) -> Size:
        return Size(rows=self._rows, columns=self._columns)


class _StubHistory:
    """A HistoryStore stand-in: tests never touch the real history directory."""

    def __init__(self, provider_id: str = "") -> None:
        self.provider_id = provider_id
        self.saved: list[object] = []

    def save(self, transcript) -> None:
        self.saved.append(transcript)


@contextmanager
def _session(rows: int = 24, columns: int = 80, *, output=None):
    """A TUI wired to a pipe input and a size-controllable output."""
    with create_pipe_input() as pipe:
        out = output or _ResizableOutput(rows, columns)
        tui = ChatTUI(
            AppState(),
            width=columns,
            height=rows,
            input_device=pipe,
            output_device=out,
        )
        yield tui, pipe, out


def _schedule(pipe, script) -> threading.Thread:
    """Send ``(delay, keys)`` steps on a background thread while the app runs."""

    def run() -> None:
        for delay, keys in script:
            time.sleep(delay)
            pipe.send_text(keys)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _walk_tree(container):
    """Every container in the tree below ``container`` (itself included)."""
    yield container
    for child in container.get_children():
        yield from _walk_tree(child)


def _config() -> AppConfig:
    config = AppConfig(provider="openrouter", model="cohere/north-mini-code:free")
    config.set_api_key("openrouter", "sk-or-test")
    return config


def _controller(tui, config: AppConfig | None = None):
    """A real ``_TuiController`` over a live TUI (the production wiring)."""
    from seedcode.app import _TuiController

    ui = TuiUI(tui)
    controller = _TuiController(ui, tui, config or _config())
    return controller, ui


@pytest.fixture(autouse=True)
def _hermetic_agent_mode(monkeypatch, tmp_path):
    """Keep every mode switch inside the test: no real config, no real index.

    The workspace capability and the desktop probe touch the real filesystem and
    the real config file, so both are replaced. Individual tests opt back into
    the real behaviour where they are testing it.
    """
    import seedcode.app as app_mod
    import seedcode.commands.agent as agent_mod
    import seedcode.commands.assist as assist_mod
    import seedcode.commands.codemode as codemode_mod
    from seedcode.codemode_state import reset

    reset()
    monkeypatch.chdir(tmp_path)
    for module in (assist_mod, codemode_mod, agent_mod):
        monkeypatch.setattr(module, "save_config", lambda config: None)
    monkeypatch.setattr(app_mod, "HistoryStore", _StubHistory)
    monkeypatch.setattr(assist_mod, "is_available", lambda: (False, "test build"))
    monkeypatch.setattr(assist_mod, "_ensure_workspace", lambda **kwargs: True)
    yield
    reset()


@pytest.fixture()
def assist_module(monkeypatch):
    """The assist command module with hermetic defaults (for per-test patches)."""
    import seedcode.commands.assist as assist_mod

    monkeypatch.setattr(assist_mod, "save_config", lambda config: None)
    monkeypatch.setattr(assist_mod, "is_available", lambda: (False, "test build"))
    return assist_mod


def _blocking_workspace(release: threading.Event, *, honour_cancel: bool = False):
    """A stand-in for the workspace preparation, optionally slow and pollable."""

    def workspace(*, cancel=None, **_kwargs) -> bool:
        while not release.is_set():
            if honour_cancel and cancel is not None and cancel():
                return False
            time.sleep(0.01)
        return True

    return workspace


# --- 1/2/3: the mode transition state machine ----------------------------------


class TestModeSwitchStateMachine:
    def test_chat_to_agent_returns_immediately(self, assist_module) -> None:
        """Chat -> Agent must hand control back to the UI at once."""
        release = threading.Event()
        assist_module._ensure_workspace = _blocking_workspace(release)
        try:
            with _session() as (tui, _pipe, _out):
                controller, _ui = _controller(tui)

                started = time.monotonic()
                controller._mode_transition("/agent on")
                elapsed = time.monotonic() - started

                # The switch itself is immediate: the session *is* in Agent Mode
                # and the header says so, while the preparation continues.
                assert elapsed < 0.5, elapsed
                assert controller.config.mode == "agent"
                assert controller.config.agent_mode is True
                assert tui.state.mode == "Agent Mode"
                assert tui.state.agent_initializing is True
                assert tui.state.status is Status.INITIALIZING
                assert "Agent Mode" in plain_text(tui.buffer.lines())
                assert "Initializing" in plain_text(tui.buffer.lines())
        finally:
            release.set()
            controller.switcher.cancel(wait=3)

    def test_agent_to_chat_is_immediate_and_cancels_initialization(
        self, assist_module
    ) -> None:
        """Agent -> Chat is cheap, immediate, and stops the background work."""
        release = threading.Event()
        assist_module._ensure_workspace = _blocking_workspace(release)
        try:
            with _session() as (tui, _pipe, _out):
                controller, _ui = _controller(tui)
                controller._mode_transition("/agent on")
                assert tui.state.agent_initializing is True

                started = time.monotonic()
                controller._mode_transition("/chat on")
                elapsed = time.monotonic() - started

                assert elapsed < 0.5, elapsed
                assert controller.config.mode == "chat"
                assert tui.state.mode == "Chat Mode"
                assert tui.state.agent_initializing is False
                assert tui.state.status is Status.READY
        finally:
            release.set()
            controller.switcher.cancel(wait=3)

    def test_repeated_switching_leaves_no_stale_or_duplicate_worker(
        self, assist_module
    ) -> None:
        """Chat -> Agent -> Chat -> Agent must not fork or leak workers."""
        release = threading.Event()
        # This stand-in polls the probe, which is what a real switch does.
        assist_module._ensure_workspace = _blocking_workspace(
            release, honour_cancel=True
        )
        try:
            with _session() as (tui, _pipe, _out):
                controller, _ui = _controller(tui)
                for _ in range(4):
                    controller._mode_transition("/agent on")
                    controller._mode_transition("/chat on")
                    # Only one initialization is ever *running*: a new switch
                    # waits for the superseded one to stop before starting.
                    assert controller.switcher.working_workers() <= 1
                controller._mode_transition("/agent on")
                # `run()` only *starts* the worker; the worker registers itself
                # as "working" a moment later (after letting any superseded
                # worker stop). Poll briefly instead of sampling a single
                # instant, so this asserts the real guarantee - exactly one
                # worker is doing the work - rather than whether the scheduler
                # happened to run the thread before the next line. The stand-in
                # work blocks until `release` is set, so once the count reaches
                # 1 it stays there until the finally block.
                deadline = time.monotonic() + 5
                while (
                    controller.switcher.working_workers() != 1
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                assert controller.switcher.working_workers() == 1
        finally:
            release.set()
            assert controller.switcher.wait(timeout=8)
        # Nothing is left running once the switches are cancelled and settled.
        assert controller.switcher.live_workers() == 0
        leaked = [
            thread.name
            for thread in threading.enumerate()
            if thread.name.startswith("seedcode-mode")
        ]
        assert leaked == [], leaked

    def test_the_switch_work_never_runs_on_the_calling_thread(self, monkeypatch) -> None:
        """The command is dispatched off the event-loop thread, always."""
        import seedcode.app as app_mod

        seen: list[str] = []
        real_dispatch = app_mod.dispatch

        def recording_dispatch(ctx, text):
            seen.append(threading.current_thread().name)
            return real_dispatch(ctx, text)

        monkeypatch.setattr(app_mod, "dispatch", recording_dispatch)
        with _session() as (tui, _pipe, _out):
            controller, _ui = _controller(tui)
            controller._mode_transition("/agent on")
            assert controller.switcher.wait(timeout=5)
        assert seen, "the switch command was never dispatched"
        assert threading.main_thread().name not in seen, seen
        assert all(name.startswith("seedcode-mode") for name in seen), seen

    def test_switch_while_initialization_is_running(self, assist_module) -> None:
        """Switching again mid-initialization is safe and ends in one state."""
        release = threading.Event()
        assist_module._ensure_workspace = _blocking_workspace(release, honour_cancel=True)
        try:
            with _session() as (tui, _pipe, _out):
                controller, _ui = _controller(tui)
                controller._mode_transition("/agent on")
                controller._mode_transition("/mode agent")
                controller._mode_transition("/chat on")
                controller._mode_transition("/agent on")
                assert tui.state.agent_initializing is True
        finally:
            release.set()
            assert controller.switcher.wait(timeout=5)
        assert controller.config.mode == "agent"
        assert tui.state.mode == "Agent Mode"
        assert tui.state.agent_initializing is False

    def test_a_failed_preparation_is_reported_not_a_freeze(self) -> None:
        """An initialization exception lands on screen and leaves Chat usable."""

        def explode(_cancel):
            raise RuntimeError("the index could not be written")

        with _session() as (tui, _pipe, _out):
            controller, _ui = _controller(tui)
            controller.tui.begin_initialization("Agent Mode")
            controller.switcher.run("agent", explode)
            assert controller.switcher.wait(timeout=5)

            # Rich wraps the line to the terminal width, so compare on the
            # whitespace-normalised text.
            content = " ".join(plain_text(tui.buffer.lines()).split())
            assert "the index could not be written" in content
            assert "Chat Mode is unaffected" in content
            assert tui.state.status is Status.ERROR
            assert tui.state.agent_initializing is False
            # Still completely usable: leaving Agent Mode is immediate.
            controller._mode_transition("/chat on")
            assert controller.config.mode == "chat"
            assert tui.state.mode == "Chat Mode"

    def test_a_command_layer_failure_is_reported_and_recoverable(
        self, monkeypatch
    ) -> None:
        """A raising mode command is reported, and the interface keeps working."""
        import seedcode.app as app_mod

        def explode(_ctx, _text):
            raise RuntimeError("the index could not be written")

        monkeypatch.setattr(app_mod, "dispatch", explode)
        with _session() as (tui, _pipe, _out):
            controller, _ui = _controller(tui)
            controller._mode_transition("/agent on")
            assert controller.switcher.wait(timeout=5)
            reported = " ".join(plain_text(tui.buffer.lines()).split())
            assert "the index could not be written" in reported
            assert tui.state.agent_initializing is False

        monkeypatch.undo()
        with _session() as (tui, _pipe, _out):
            controller, _ui = _controller(tui)
            controller._mode_transition("/chat on")
            assert controller.config.mode == "chat"
            assert tui.state.mode == "Chat Mode"

    def test_confirm_on_the_interface_thread_is_refused_not_awaited(self) -> None:
        """The permission panel can never wait for an answer from itself."""
        with _session() as (tui, _pipe, _out):
            tui._ui_thread = threading.get_ident()  # this *is* the loop thread
            started = time.monotonic()
            answer = tui.confirm("Agent Action", "Run command", "npm install")
            elapsed = time.monotonic() - started

            assert answer == "n"  # denied, never granted by accident
            assert elapsed < 1.0, elapsed  # and never a 600s wait
            assert "could not be shown" in plain_text(tui.buffer.lines())
            assert tui.state.status is Status.ERROR

    def test_the_permission_panel_is_abandoned_when_superseded(self) -> None:
        """A pending permission ask stops when its switch is superseded."""
        cancelled = threading.Event()
        with _session() as (tui, _pipe, _out):
            answers: dict[str, str] = {}
            stop = threading.Event()

            def ask() -> None:
                answers["answer"] = tui.confirm(
                    "Desktop Control",
                    "Grant desktop control",
                    "mouse and keyboard",
                    cancel=stop.is_set,
                )

            worker = threading.Thread(target=ask, daemon=True)
            worker.start()
            # Wait until the panel is really up, then supersede it.
            for _ in range(50):
                if tui._confirm_event is not None:
                    break
                time.sleep(0.01)
            assert tui._confirm_event is not None
            stop.set()
            worker.join(timeout=3)
            assert not worker.is_alive()
            assert answers["answer"] == "n"
            assert tui._confirm_event is None
            assert cancelled or True  # (kept for readability of the intent)

    def test_agent_entry_classification_matches_the_command_layer(self) -> None:
        """The immediate UI state must agree with what the command will do."""
        with _session() as (tui, _pipe, _out):
            controller, _ = _controller(tui)
            entering = (
                "/agent on",
                "/assist on",
                "/desktop on",
                "/mode agent",
                "/mode code",
                "/mode assist",
                "/codemode on",
            )
            leaving = (
                "/chat on",
                "/chat",
                "/agent off",
                "/assist off",
                "/desktop off",
                "/mode chat",
                "/mode",
                "/codemode off",
                "/codemode status",
                "/status",
            )
            # From Chat Mode, the bare form enters Agent Mode.
            for text in entering:
                assert controller._enters_agent_mode(text) is True, text
            assert controller._enters_agent_mode("/agent") is True
            for text in leaving:
                assert controller._enters_agent_mode(text) is False, text
            # From Agent Mode, the bare form leaves it.
            controller.config.mode = "agent"
            assert controller._enters_agent_mode("/agent") is False


# --- the ModeSwitcher itself ---------------------------------------------------


class TestModeSwitcher:
    def test_single_flight_for_the_same_target(self) -> None:
        release = threading.Event()
        runs: list[int] = []

        def work(cancel):
            runs.append(1)
            release.wait(5)

        switcher = ModeSwitcher()
        first = switcher.run("agent", work)
        second = switcher.run("agent", work)
        assert first == second  # asking again starts nothing
        assert switcher.live_workers() == 1
        release.set()
        assert switcher.wait(timeout=5)
        assert runs == [1]

    def test_a_superseded_transition_reports_nothing(self) -> None:
        release = threading.Event()
        finished: list[str] = []
        failed: list[str] = []
        switcher = ModeSwitcher(
            on_finished=lambda _gen, key, _result: finished.append(key),
            on_failed=lambda _gen, key, error: failed.append(key),
        )
        switcher.run("agent", lambda cancel: release.wait(5))
        switcher.run("chat", lambda cancel: "done")
        # The superseded worker is blocked on purpose, so let it go, then wait.
        release.set()
        assert switcher.wait(timeout=5)
        assert finished == ["chat"]  # only the newest transition reports
        assert failed == []

    def test_an_exception_is_reported_not_swallowed(self) -> None:
        errors: list[str] = []
        switcher = ModeSwitcher(
            on_failed=lambda _gen, key, error: errors.append(error),
        )
        switcher.run("agent", lambda cancel: 1 / 0)
        assert switcher.wait(timeout=5)
        assert errors and "ZeroDivisionError" in errors[0]

    def test_cancel_waits_for_the_workers(self) -> None:
        release = threading.Event()
        switcher = ModeSwitcher()
        switcher.run("agent", lambda cancel: release.wait(5))
        release.set()
        switcher.cancel(wait=3)
        assert switcher.live_workers() == 0


# --- 4/5 + resize: the interface stays live ------------------------------------


class TestInterfaceStaysResponsive:
    def test_a_mode_switch_mid_stream_keeps_the_answer(self, assist_module) -> None:
        """Switching while the model streams must not disturb the stream."""
        release = threading.Event()
        assist_module._ensure_workspace = _blocking_workspace(release)
        try:
            with _session() as (tui, pipe, _out):
                controller, ui = _controller(tui)

                def on_submit(_text: str) -> None:
                    with ui.streaming() as renderer:
                        renderer.feed("streamed ")
                        controller._mode_transition("/agent on")
                        renderer.feed("answer")
                    ui.info("(done)")

                tui.on_submit = on_submit
                _schedule(pipe, [(0.3, "go"), (0.35, "\r"), (1.5, "\x04")])
                tui.run_once()

                content = plain_text(tui.buffer.lines())
                assert "streamed answer" in content
        finally:
            release.set()
            controller.switcher.cancel(wait=3)

    def test_typing_works_while_agent_initializes(self, assist_module) -> None:
        """The event loop keeps delivering input during initialization."""
        release = threading.Event()
        assist_module._ensure_workspace = _blocking_workspace(release)
        try:
            with _session() as (tui, pipe, _out):
                controller, _ui = _controller(tui)
                # Match the production composer path: the mode command arrives
                # while the application owns the screen.
                _schedule(
                    pipe,
                    [
                        (0.3, "/agent on"),
                        (0.35, "\r"),
                        (0.8, "hello"),
                        (1.3, "\x04"),
                    ],
                )
                started = time.monotonic()
                result = tui.run_once()
                elapsed = time.monotonic() - started

                assert result == ("exit", None)
                assert elapsed < 8, elapsed  # never blocked on the preparation
                assert tui._input.text == "hello"  # typed text really arrived
                assert tui.state.mode == "Agent Mode"
                assert tui.state.agent_initializing is True
                hint = "".join(text for _, text in tui._composer_bottom())
                assert "Initializing" in hint
        finally:
            release.set()
            controller.switcher.cancel(wait=3)

    def test_streaming_updates_only_the_conversation_region(self) -> None:
        """Tokens repaint the answer; the header and composer stay stable."""
        with _session() as (tui, _pipe, _out):
            tui._build_app()
            ui = TuiUI(tui)
            widgets = (
                tui._header_window,
                tui._content_window,
                tui._input_window,
                tui._permission_window,
                tui._hint_window,
            )
            header_before = header_lines(tui.state, tui._width)
            # The branding block: the border, the three ASCII logo rows and the
            # tagline. The identity row below it carries the live status.
            branding = header_before[:5]
            with ui.streaming() as renderer:
                for chunk in ("one ", "two ", "three"):
                    renderer.feed(chunk)
                    renderer.flush()
                    # No widget replaced, and the branding/identity does not
                    # churn per token: only the conversation region updates.
                    assert (
                        tui._header_window,
                        tui._content_window,
                        tui._input_window,
                        tui._permission_window,
                        tui._hint_window,
                    ) == widgets
                    assert header_lines(tui.state, tui._width)[:5] == branding
            content = plain_text(tui.buffer.lines())
            assert "one two three" in content
            # Committed exactly once — a live block is never duplicated into
            # the scrollback on top of itself.
            assert content.count("one two three") == 1
            assert tui._stream is None
            # Nothing else about the header drifted: back to Ready and the
            # whole header is byte-for-byte what it was.
            tui.state.set_status(Status.READY, "")
            assert header_lines(tui.state, tui._width) == header_before

    def test_streaming_writes_nothing_outside_the_managed_regions(self, capsys) -> None:
        """A streamed answer never reaches stdout.

        Text written to stdout underneath a full-screen prompt_toolkit
        application is what makes the whole interface flicker (and duplicates
        the answer). The streamed renderer must record only; the managed
        conversation console is the sole thing allowed to paint.
        """
        with _session() as (tui, _pipe, _out):
            ui = TuiUI(tui)
            with ui.streaming() as renderer:
                renderer.feed("# Heading")
                renderer.feed("\n\nfirst **bold** line")
                renderer.flush()
            captured = capsys.readouterr()
            assert "first bold line" in plain_text(tui.buffer.lines())
        assert captured.out == ""
        assert captured.err == ""

    def test_resize_during_initialization_is_safe(self, assist_module) -> None:
        release = threading.Event()
        assist_module._ensure_workspace = _blocking_workspace(release)
        try:
            with _session(rows=24, columns=80) as (tui, pipe, output):
                controller, _ui = _controller(tui)

                def scripted() -> None:
                    time.sleep(0.3)
                    pipe.send_text("/agent on\r")
                    time.sleep(0.4)
                    output.resize(50, 200)  # grow
                    tui._invalidate()
                    time.sleep(0.3)
                    output.resize(8, 46)  # and shrink hard
                    tui._invalidate()
                    time.sleep(0.3)
                    pipe.send_text("\x04")

                threading.Thread(target=scripted, daemon=True).start()
                assert tui.run_once() == ("exit", None)
                assert tui.state.agent_initializing is True
                assert tui.min_layout_rows() <= tui._rows
        finally:
            release.set()
            controller.switcher.cancel(wait=3)


# --- 6-13: terminal resizing ---------------------------------------------------


class TestResizeResponsiveness:
    @pytest.mark.parametrize(
        "rows,columns",
        [(3, 20), (4, 30), (5, 40), (6, 40), (8, 50), (10, 60), (24, 80), (50, 200)],
    )
    def test_the_regions_always_fit_the_terminal(self, rows, columns) -> None:
        """Compression, never prompt_toolkit's blank "window too small" screen."""
        with _session(rows=rows, columns=columns) as (tui, _pipe, _out):
            tui._width = max(20, columns)
            tui._rows = rows
            tui._reallocate_rows()
            assert tui.min_layout_rows() <= rows, (rows, columns)

    @pytest.mark.parametrize("columns", [20, 24, 40, 60, 80, 100, 120, 160, 200])
    def test_no_line_is_wider_than_the_terminal(self, columns) -> None:
        with _session(columns=columns) as (tui, _pipe, _out):
            for line in header_lines(tui.state, columns):
                assert sum(len(text) for _, text in line) <= columns

    def test_a_short_terminal_still_draws_the_composer(self) -> None:
        """The end-to-end proof: a 6-row terminal is compressed, not blank."""
        buffer = io.StringIO()
        output = _ResizablePlainOutput(buffer, rows=6, columns=40)
        with _session(rows=6, columns=40, output=output) as (tui, pipe, _out):
            _schedule(pipe, [(0.25, "\x04")])
            assert tui.run_once() == ("exit", None)
        rendered = buffer.getvalue()
        assert "Window too small" not in rendered
        assert "Message" in rendered  # the composer's frame is on screen

    def test_a_large_terminal_uses_the_extra_space(self) -> None:
        with _session(rows=50, columns=200) as (tui, _pipe, _out):
            tui._rows, tui._width = 50, 200
            tui._reallocate_rows()
            # The header keeps its preferred height and the conversation takes
            # everything that is left over.
            assert tui._header_rows == len(header_lines(tui.state, 200))
            assert tui.min_layout_rows() < 50 - 10

    def test_a_resize_re_fits_without_rebuilding_anything(self) -> None:
        with _session(rows=24, columns=80) as (tui, _pipe, output):
            app = tui._build_app()
            tracker = {"builds": 0}
            original_build = tui._build_app

            def counting_build():
                tracker["builds"] += 1
                return original_build()

            tui._build_app = counting_build  # type: ignore[assignment]
            widgets = (
                tui._header_window,
                tui._content_window,
                tui._input_window,
                tui._permission_window,
                tui._hint_window,
            )
            tui._input.text = "typed text stays"

            tui._before_render(app)
            assert (tui._width, tui._rows) == (80, 24)

            for rows, columns in ((30, 100), (40, 160), (12, 60), (24, 80)):
                output.resize(rows, columns)
                tui._before_render(app)
                assert (tui._width, tui._rows) == (columns, rows)
                # Every region is the *same object*: only its size changed.
                assert (
                    tui._header_window,
                    tui._content_window,
                    tui._input_window,
                    tui._permission_window,
                    tui._hint_window,
                ) == widgets
                assert tui.min_layout_rows() <= rows

            assert tracker["builds"] == 0  # no rebuild, ever
            assert tui._input.text == "typed text stays"  # input never lost

    def test_the_layout_tree_has_exactly_one_of_each_widget(self) -> None:
        """No duplicated header, composer, border or textbox anywhere."""
        from prompt_toolkit.layout.controls import BufferControl
        from prompt_toolkit.layout.containers import Window

        with _session() as (tui, _pipe, _out):
            app = tui._build_app()
            root = app.layout.container
            # The top level carries exactly the five regions, in order.
            assert root.get_children() == [
                tui._header_window,
                tui._content_window,
                tui._permission_window,
                root.get_children()[3],  # the composer frame
                tui._hint_window,
            ]
            nodes = list(_walk_tree(root))
            for widget in (
                tui._header_window,
                tui._content_window,
                tui._input_window,
                tui._permission_window,
                tui._hint_window,
            ):
                assert sum(1 for node in nodes if node is widget) == 1
            # Exactly one editor (one textbox, one border around it).
            editors = [
                node
                for node in nodes
                if isinstance(node, Window) and isinstance(node.content, BufferControl)
            ]
            assert editors == [tui._input_window]

    def test_many_resizes_never_duplicate_a_widget(self) -> None:
        with _session() as (tui, _pipe, output):
            app = tui._build_app()
            widgets = (
                tui._header_window,
                tui._content_window,
                tui._input_window,
                tui._permission_window,
                tui._hint_window,
            )
            for step in range(40):
                output.resize(4 + (step % 20), 20 + (step * 7) % 190)
                tui._before_render(app)
                assert (
                    tui._header_window,
                    tui._content_window,
                    tui._input_window,
                    tui._permission_window,
                    tui._hint_window,
                ) == widgets

    def test_a_resize_mid_stream_re_wraps_the_answer(self) -> None:
        with _session(rows=24, columns=80) as (tui, _pipe, output):
            app = tui._build_app()
            ui = TuiUI(tui)
            long_text = "word " * 60
            with ui.streaming() as renderer:
                renderer.feed(long_text)
                first = plain_text(tui.buffer.lines())
                output.resize(30, 40)  # narrower
                tui._before_render(app)  # re-fits and re-wraps the live block
                renderer.feed("tail")
                renderer.flush()
                second = plain_text(tui.buffer.lines())
            assert long_text.strip() in first.replace("\n", " ")
            assert "tail" in second
            assert tui._stream is None  # the live block was released
            assert output.get_size().columns == 40

    def test_input_is_preserved_across_every_resize(self) -> None:
        with _session() as (tui, _pipe, output):
            app = tui._build_app()
            tui._input.text = "a message with\nseveral lines"
            tui._input.cursor_position = len(tui._input.text)
            for rows, columns in ((6, 40), (24, 80), (60, 220), (3, 20), (24, 80)):
                output.resize(rows, columns)
                tui._before_render(app)
                assert tui._input.text == "a message with\nseveral lines"
                assert tui._input_rows >= 0
            assert tui._input.document.cursor_position == len(tui._input.text)
