"""The persistent Seed Code terminal interface (v8.2.5).

A real terminal application instead of a sequence of printed banners. The
screen is split into exactly three regions:

* a **fixed header** — the live dashboard (provider, model, mode, status,
  workspace, context budget, masked key), rendered from
  :class:`~seedcode.ui.state.AppState` on every frame so a change is visible
  immediately;
* a **scrolling middle** — the conversation, live agent activity, tool and
  command output. It follows the bottom while the user is at the bottom, and
  stops following the moment the user scrolls up, so reading history is never
  interrupted by new output;
* a **fixed composer** — a real, bounded multiline editor. The border is drawn
  by prompt_toolkit's ``Frame`` around the ``BufferControl`` (never by manually
  printed border text), so the ``╭─ Message ─╮`` box is the actual visual
  boundary: long lines wrap inside it, taller content scrolls internally, and
  typed text can never leak outside the border. ``Enter`` sends,
  ``Shift+Enter`` inserts a newline, ``↑``/``↓`` move the cursor inside a
  multiline message (and walk the input history at the edges), and ``Esc``
  clears.

The single source of session truth is the **header** dashboard: provider,
model, mode and status each appear in exactly one place — there is no second
toolbar repeating them, and no separate Submit button competing with Enter.

Before the first token arrives the content region shows an animated
``AI > ◌ Thinking…`` line. It is a UI state only — the animation runs on its own
thread, is replaced the instant real output starts, and never delays the model
call or the agent.

Everything the existing application prints already flows here: the TUI owns a
Rich console whose stream is a sink feeding the content buffer, so commands,
menus and engine messages keep working unchanged. Streaming and spinners do not
use Rich's ``Live`` under the TUI; they update the live block and let
prompt_toolkit repaint incrementally, which is what removes the flicker.

The app runs synchronously on the main thread (prompt_toolkit requires it) while
a turn executes on a worker thread; state changes from the worker request a
repaint through :meth:`Application.invalidate` on the application's event loop.
Ctrl+C during a turn is cooperative: the UI methods check the cancel flag and
raise :class:`KeyboardInterrupt` inside the running turn, which the turn's own
handler already treats as a cancellation.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl, UIControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import DynamicStyle
from prompt_toolkit.widgets import Frame
from rich.console import Console

from ..utils.text import safe_text
from .content import ContentBuffer, StyledLine, trim_line
from .header import header_lines
from .state import AppState, Status
from .theme import pt_style, rich_theme

__all__ = ["ChatTUI", "TuiUI", "TuiStreamRenderer"]

#: Repaint the live block at most this often while streaming (never per token).
_STREAM_INTERVAL_S = 1 / 15
#: The composer always shows at least this many rows (room for a wrapped line),
#: grows with the message, and starts scrolling internally past the maximum.
_MIN_INPUT_LINES = 2
_MAX_INPUT_LINES = 6
#: Rows of the bounded permission panel (shown only while a request waits).
_PERMISSION_HEIGHT = 7

#: The composer's in-box prompt: a chevron with a leading and trailing cell so a
#: wrapped continuation line stays indented under the first one. Its length is
#: the width of the dedicated prompt column inside the frame.
_COMPOSER_PROMPT = " \u203a "
#: One blank cell of right padding inside the composer frame.
_COMPOSER_PAD = 1

#: The conversation label echoed before a user message in the scrollback. It is
#: a display-only attribution for the chat history: the composer itself carries
#: no such prefix, so the submitted message is never decorated with it.
_INPUT_PROMPT = "You > "
#: Every assistant line (activity and streamed answer) is attributed with this.
_AI_PREFIX = "AI > "

#: Frames of the pre-response thinking indicator (dots that grow, then reset).
_THINK_FRAMES = (".", "..", "...")
#: Animation cadence: alive enough to reassure, calm enough not to distract.
_THINK_INTERVAL_S = 0.4


def _agentic_mode(mode: str) -> bool:
    """Whether ``mode`` is Agent Mode (the only mode that acts on the project)."""
    return str(mode).startswith("Agent")


#: Commands that are pure mode switches / state transitions. They are dispatched
#: in place so switching modes never tears down the application or event loop.
_MODE_COMMANDS = frozenset(
    {"agent", "assist", "chat", "mode", "codemode", "desktop", "status"}
)


def _is_mode_command(text: str) -> bool:
    """Whether a slash command is a safe, in-place mode/state transition."""
    parts = text.strip().lstrip("/").split(maxsplit=1)
    return bool(parts) and parts[0].lower() in _MODE_COMMANDS


class _ConsoleSink:
    """A file-like stream that feeds everything written to it into a buffer."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, buffer: ContentBuffer) -> None:
        self._buffer = buffer

    def write(self, text: str) -> int:
        if text:
            self._buffer.write(text)
        return len(text) if text else 0

    def flush(self) -> None:  # pragma: no cover - nothing to flush
        pass

    def isatty(self) -> bool:
        return False


class _TuiConsole(Console):
    """A Rich console whose ``clear()`` clears the content region, not the terminal.

    ``/clear`` must not emit an ANSI clear into a buffer that owns the screen;
    it empties the scrollback instead.
    """

    def __init__(self, *args, on_clear: Callable[[], None] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._on_clear = on_clear

    def clear(self, home: bool = True) -> None:  # type: ignore[override]
        if self._on_clear is not None:
            self._on_clear()
            return
        super().clear(home)


class _ContentControl(UIControl):
    """A non-focusable control that renders the buffer's visible slice."""

    def __init__(
        self,
        provider: Callable[[int, int], list[StyledLine]],
        mouse: Callable[[Any], Any] | None = None,
    ) -> None:
        self._provider = provider
        self._mouse = mouse

    def is_focusable(self) -> bool:
        return False

    def is_scrollable(self) -> bool:
        return False

    def mouse_handler(self, mouse_event):
        if self._mouse is not None:
            return self._mouse(mouse_event)
        return NotImplemented

    def create_content(self, width: int, height: int):
        try:
            lines = self._provider(width, height)
        except Exception:
            lines = [[("", "(display error)")]]
        flat: list[tuple[str, str]] = []
        for index, line in enumerate(lines):
            if index:
                flat.append(("", "\n"))
            flat.extend(line)
        return FormattedTextControl(flat, show_cursor=False).create_content(width, height)


class TuiStreamRenderer:
    """Streamed assistant tokens, painted into the live content block."""

    def __init__(self, tui: "ChatTUI") -> None:
        self._tui = tui
        self._buffer = ""
        self._last = 0.0

    def feed(self, chunk: str) -> None:
        self._buffer += safe_text(chunk)
        now = time.monotonic()
        if now - self._last >= _STREAM_INTERVAL_S:
            self._last = now
            self._tui.set_stream(self._buffer)

    def flush(self) -> None:
        self._tui.set_stream(self._buffer, force=True)

    @property
    def text(self) -> str:
        return self._buffer


class ChatTUI:
    """Owns the prompt_toolkit application, the state and the content buffer."""

    def __init__(
        self,
        state: AppState,
        *,
        workspace: str = "",
        width: int = 80,
        input_device: Any = None,
        output_device: Any = None,
    ) -> None:
        self.state = state
        self._input_device = input_device
        self._output_device = output_device
        self._width = max(20, int(width))
        self.on_submit: Callable[[str], None] | None = None
        #: Dispatch a mode-switch command in place (no terminal takeover, no
        #: application restart). Set by the controller; falls back to leaving
        #: the application when unset (the plain/legacy path).
        self.on_command: Callable[[str], None] | None = None
        self.buffer = ContentBuffer(on_change=self._invalidate)
        self.console = _TuiConsole(
            file=_ConsoleSink(self.buffer),
            on_clear=self._clear_screen,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
            width=self._width,
            theme=rich_theme(),
            highlight=False,
        )
        self._input = Buffer(multiline=True, accept_handler=self._accept)
        self._app: Application | None = None
        self._header_window: Window | None = None
        self._input_window: Window | None = None
        self._content_window: Window | None = None
        self._scroll = 0
        self._follow = True
        #: Lines below the viewport while the user is scrolled up (a hint only).
        self._unseen = 0
        self._history: list[str] = []
        self._history_index = 0
        self._draft = ""
        self._busy = False
        self._cancel = threading.Event()
        self._confirm_lock = threading.Lock()
        self._confirm_event: threading.Event | None = None
        self._confirm_answer: str = "n"
        self._confirm_prompt: tuple[str, str] | None = None
        #: The full pending request, rendered as the bounded permission panel.
        self._confirm_detail: tuple[str, str, str] | None = None
        self._permission_window: Window | None = None
        # --- thinking indicator (cosmetic; never blocks the turn) -----------
        self._think_lock = threading.Lock()
        self._think_stop: threading.Event | None = None
        self._think_thread: threading.Thread | None = None
        self._think_label = "Thinking"
        self._think_index = 0
        self._thinking_active = False
        state.subscribe(lambda _state: self._invalidate())

    def _clear_screen(self) -> None:
        self.buffer.clear()
        self._follow = True
        self._invalidate()

    # --- content API --------------------------------------------------------
    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def request_cancel(self) -> None:
        if self._busy:
            self._cancel.set()
            self.state.set_status(Status.CANCELLED, "cancelling")

    def check_cancel(self) -> None:
        """Raise inside the running turn when the user asked to stop."""
        if self._cancel.is_set():
            raise KeyboardInterrupt

    def append_text(self, text: str, style: str = "") -> None:
        self.console.print(safe_text(text), style=style or None, highlight=False)

    def append_block(self, text: str, style: str = "") -> None:
        """Append a labeled paragraph followed by a blank line."""
        self.console.print(safe_text(text), style=style or None, highlight=False)
        self.console.print()

    def append_user(self, text: str) -> None:
        """Echo the user's turn behind the ``You >`` prompt.

        Continuation lines of a multiline message are indented under the prompt
        so the message stays visually attached to it. The stored/engine text is
        untouched: the prefix exists only on screen.
        """
        from .theme import active_palette

        palette = active_palette()
        lines = safe_text(text).splitlines() or [""]
        indent = " " * len(_INPUT_PROMPT)
        self.console.print()
        self.console.print(
            f"{_INPUT_PROMPT}{lines[0]}",
            style=f"bold {palette.primary}",
            highlight=False,
        )
        for extra in lines[1:]:
            self.console.print(f"{indent}{extra}", highlight=False)
        self.console.print()

    def append_activity(self, kind: str, text: str, *, legacy: bool = False) -> None:
        """One live activity line, attributed to the assistant.

        Rendered as ``AI > ⚙ Reading package.json``. ``kind`` is ``action``
        (started), ``success``, ``error`` or ``note``. Only real events are
        passed here — the stream never invents progress.
        """
        from rich.text import Text

        from .theme import active_palette

        palette = active_palette()
        marks = {
            "action": ("\u2699", palette.accent),
            "success": ("\u2713", palette.success),
            "error": ("\u2715", palette.error),
            "note": ("\u00b7", palette.dim),
        }
        mark, colour = marks.get(kind, marks["note"])
        line = Text()
        line.append(_AI_PREFIX, style=f"bold {palette.primary}")
        line.append(f"{mark} ", style=colour)
        line.append(safe_text(text), style=colour)
        self.console.print(line, highlight=False)

    def set_stream(self, text: str, *, force: bool = False) -> None:
        """Render the streamed markdown into the live block (throttled)."""
        if not text and not force:
            return
        if not text:
            self.buffer.clear_live()
            return
        self.buffer.set_live(_assistant_lines(_markdown_lines(text, self._width)))

    def finish_stream(self) -> None:
        self.buffer.commit_live()
        self.console.print()

    # --- thinking indicator (UI state only) ---------------------------------
    def _thinking_lines(self) -> list[StyledLine]:
        frame = _THINK_FRAMES[self._think_index % len(_THINK_FRAMES)]
        palette = _palette()
        return [
            [
                (f"bold fg:{palette.primary}", _AI_PREFIX),
                (f"fg:{palette.accent}", f"\u25cc {self._think_label}{frame}"),
            ]
        ]

    def _render_thinking(self) -> None:
        self.buffer.set_live(self._thinking_lines())
        self._invalidate()

    def start_thinking(self, label: str = "Thinking") -> None:
        """Show the animated ``AI > ◌ Thinking…`` line (idempotent).

        The animation is a UI state only: it lives on a daemon thread, is
        stopped the moment real output starts, and never delays the turn.
        """
        with self._think_lock:
            self._think_label = (label or "").strip() or "Thinking"
            self._think_index = 0
            self._thinking_active = True
            self._render_thinking()
            thread = self._think_thread
            if thread is not None and thread.is_alive():
                return  # already animating — only the label needed refreshing
            stop = threading.Event()
            self._think_stop = stop
            self._think_thread = threading.Thread(
                target=self._thinking_loop,
                args=(stop,),
                name="seedcode-thinking",
                daemon=True,
            )
            self._think_thread.start()

    def _thinking_loop(self, stop: threading.Event) -> None:
        while not stop.wait(_THINK_INTERVAL_S):
            with self._think_lock:
                if stop.is_set() or not self._thinking_active:
                    return
                self._think_index += 1
                self._render_thinking()

    def stop_thinking(self) -> None:
        """Stop the animation and drop its placeholder (a safe no-op if idle).

        Guarded so a late ``stop_thinking`` (the turn's ``finally``) can never
        wipe a live stream block that already replaced the indicator.
        """
        with self._think_lock:
            if not self._thinking_active:
                return
            self._thinking_active = False
            stop = self._think_stop
            self._think_stop = None
            self._think_thread = None
        if stop is not None:
            stop.set()
        self.buffer.clear_live()
        self._invalidate()

    # --- state --------------------------------------------------------------
    def sync_from_config(self, config: Any) -> None:
        self.state.sync_from_config(config)
        self._invalidate()

    # --- scrolling ----------------------------------------------------------
    def scroll_by(self, delta: int) -> None:
        self._follow = False
        self._scroll = max(0, self._scroll + delta)
        self._invalidate()

    def scroll_to_bottom(self) -> None:
        self._follow = True
        self._invalidate()

    def _slice(self, width: int, height: int) -> list[StyledLine]:
        lines = self.buffer.lines()
        total = len(lines)
        height = max(1, height)
        max_scroll = max(0, total - height)
        if self._follow:
            self._scroll = max_scroll
        else:
            self._scroll = min(self._scroll, max_scroll)
            if self._scroll >= max_scroll:
                self._follow = True
        # While the user reads history, remember how much arrived below the
        # viewport so the composer can hint at it (never yanking them back).
        self._unseen = 0 if self._follow else max(0, max_scroll - self._scroll)
        return lines[self._scroll : self._scroll + height]

    # --- composer -----------------------------------------------------------
    def _input_height(self) -> int:
        lines = self._input.text.count("\n") + 1
        return max(_MIN_INPUT_LINES, min(lines, _MAX_INPUT_LINES))

    # --- composer prompt ----------------------------------------------------
    def _prompt_fragments(self) -> list[tuple[str, str]]:
        """The composer's in-box chevron, drawn in its own fixed-width column."""
        from .theme import active_palette

        return [(f"bold fg:{active_palette().primary}", _COMPOSER_PROMPT)]

    def _submit_text(self, text: str) -> None:
        text = (text or "").strip()
        if self._confirm_event is not None:
            self._resolve_confirm(text)
            return
        self._input.text = ""
        if not text:
            return
        self._remember(text)
        if text.startswith("/"):
            # Mode switches are pure state transitions: they run inside the
            # live application (no exit, no rebuild, no flicker). Everything
            # else may open a selector/menu that needs the terminal to itself,
            # so it hands control back to the controller for one dispatch.
            if self.on_command is not None and _is_mode_command(text):
                self._start_command(text)
                return
            self._leave(("command", text))
            return
        # A message runs while the application keeps owning the screen, so the
        # header stays fixed and streamed output updates the middle region.
        self._start_turn(text)

    def _start_command(self, text: str) -> None:
        """Run a mode-switch command on a worker thread (UI stays live)."""
        def run() -> None:
            try:
                if self.on_command is not None:
                    self.on_command(text)
            except Exception as exc:  # a command must never end the session
                self.append_activity("error", f"{type(exc).__name__}: {exc}")
            finally:
                self._invalidate()

        threading.Thread(
            target=run, name="seedcode-command", daemon=True
        ).start()

    def _remember(self, text: str) -> None:
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._history_index = 0
        self._draft = ""

    def _history_move(self, step: int) -> None:
        if not self._history:
            return
        if step < 0:
            if self._history_index == 0:
                self._draft = self._input.text
            self._history_index = min(len(self._history), self._history_index + 1)
            self._input.text = self._history[len(self._history) - self._history_index]
        else:
            if self._history_index <= 0:
                return
            self._history_index -= 1
            if self._history_index == 0:
                self._input.text = self._draft
            else:
                self._input.text = self._history[len(self._history) - self._history_index]

    def _accept(self, buffer: Buffer) -> bool:
        self._submit_text(buffer.text)
        return True

    # --- confirmation (permissions) -----------------------------------------
    def confirm(self, title: str, question: str, description: str) -> str:
        """Ask from the worker thread; answer on the main thread. Deny on timeout.

        The request is rendered as a bounded, keyboard-driven panel inside the
        existing application (never a nested prompt, never a blocking
        ``input()``): only the agent turn waits, and ``Enter`` allows, ``A``
        allows for the session, ``D`` denies and ``Esc`` cancels.
        """
        event = threading.Event()
        with self._confirm_lock:
            self._confirm_event = event
            self._confirm_answer = "n"
            self._confirm_prompt = (title, question)
            self._confirm_detail = (title, question, description)
        self._input.text = ""
        self.state.set_status(Status.WAITING, description or question)
        self._invalidate()
        # Wait in short slices: a Ctrl+C / Esc cancellation (which sets the
        # turn's cancel flag) must turn into an immediate deny rather than
        # blocking the turn until the timeout fires.
        answered = False
        deadline = time.monotonic() + 600
        try:
            while not answered:
                if event.wait(timeout=0.1):
                    answered = True
                    break
                if self._cancel.is_set() or time.monotonic() >= deadline:
                    break
        finally:
            with self._confirm_lock:
                self._confirm_event = None
                self._confirm_prompt = None
                self._confirm_detail = None
        self.state.set_status(Status.WORKING)
        self._invalidate()
        return self._confirm_answer if answered else "n"

    def _resolve_confirm(self, text: str) -> None:
        """Map a permission answer to the grant the engine understands.

        ``y`` / Enter -> allow once, ``a`` / ``Y`` -> allow for this session,
        ``d`` / ``n`` / Esc -> deny. Unknown input denies, so a stray key can
        never silently grant a dangerous action.
        """
        key = (text or "").strip().lower()[:1]
        answer = {"y": "y", "a": "a", "d": "n", "n": "n"}.get(key, "")
        if not key:
            answer = "y"  # Enter allows (the panel's primary action)
        if not answer:
            answer = "n"
        with self._confirm_lock:
            self._confirm_answer = answer
            event = self._confirm_event
        self._input.text = ""
        if event is not None:
            event.set()
        self._invalidate()

    def _deny_confirm(self) -> bool:
        if self._confirm_event is None:
            return False
        self._resolve_confirm("n")
        return True

    # --- permission panel (a bounded TUI component) -------------------------
    def _permission_height(self) -> int:
        """Rows reserved for the permission panel (0 when no request waits)."""
        return _PERMISSION_HEIGHT if self._confirm_detail is not None else 0

    def _permission_fragments(self) -> list[tuple[str, str]]:
        """The bounded ``┌─ Permission Required ─┐`` panel, from live state.

        It never writes into the conversation: the panel is its own region, so
        the header, the scrollback and the composer all stay stable while the
        user decides.
        """
        from .theme import active_palette

        palette = active_palette()
        detail = self._confirm_detail
        if detail is None:
            return []
        title, question, description = detail
        width = max(20, self._width)
        inner = max(1, width - 2)
        box = {"tl": "\u256d", "tr": "\u256e", "bl": "\u2570", "br": "\u256f", "h": "\u2500", "v": "\u2502"}
        bar = f"fg:{palette.dim}"
        label = f"\u2500 {title} "
        top = box["tl"] + label + box["h"] * max(0, width - 2 - len(label)) + box["tr"]
        bottom = box["bl"] + box["h"] * (width - 2) + box["br"]

        def row(fragments: list[tuple[str, str]]) -> list[tuple[str, str]]:
            used = sum(len(t) for _, t in fragments)
            padded = [(style, text) for style, text in fragments]
            if used < inner:
                padded.append(("", " " * (inner - used)))
            return [(bar, box["v"])] + padded + [(bar, box["v"])]

        def clip(text: str, limit: int) -> str:
            # Collapse whitespace (including newlines) so a multi-line request
            # can never escape the panel's fixed height.
            text = " ".join(safe_text(text).split())
            if limit <= 0:
                return ""
            return text if len(text) <= limit else text[: max(0, limit - 1)] + "\u2026"

        lines: list[list[tuple[str, str]]] = [
            [(bar, top)],
            row([(f"fg:{palette.text}", " Agent wants to run:")]),
            row([(f"bold fg:{palette.warning}", " " + clip(description or question, inner - 2))]),
            row([(f"fg:{palette.dim}", " Reason: " + clip(question or title, inner - 10))]),
            row(
                [
                    (f"fg:{palette.dim}", "  "),
                    (f"bold fg:{palette.success}", "[A]"),
                    (f"fg:{palette.text}", " Allow   "),
                    (f"bold fg:{palette.error}", "[D]"),
                    (f"fg:{palette.text}", " Deny   "),
                    (f"bold fg:{palette.accent}", "[Y]"),
                    (f"fg:{palette.text}", " Allow session"),
                ]
            ),
            row([(f"fg:{palette.dim}", "  Enter \u2192 Allow   Esc \u2192 Deny")]),
            [(bar, bottom)],
        ]
        flat: list[tuple[str, str]] = []
        for index, line in enumerate(lines):
            if index:
                flat.append(("", "\n"))
            flat.extend(line)
        return flat

    # --- turn execution -----------------------------------------------------
    def run_turn(self, text: str) -> None:
        """Run one user turn on a worker thread, keeping the UI live."""
        self._busy = True
        self._cancel.clear()
        self.append_user(text)
        self.state.add_message("user", text)
        # Thinking starts the moment the message is sent — before the provider
        # has been called — and is replaced by the answer as soon as it streams.
        self.state.set_status(Status.THINKING, "composing a reply")
        self.start_thinking("Thinking")
        self._invalidate()
        try:
            if self.on_submit is not None:
                self.on_submit(text)
            self.state.set_status(
                Status.COMPLETED if _agentic_mode(self.state.mode) else Status.READY, ""
            )
        except KeyboardInterrupt:
            self.state.set_status(Status.CANCELLED, "cancelled")
        except Exception as exc:  # one failed turn never ends the session
            self.state.set_status(Status.ERROR, str(exc))
            self.append_activity("error", f"{type(exc).__name__}: {exc}")
        finally:
            self.stop_thinking()
            self._busy = False
            self._cancel.clear()
            self._invalidate()

    def _start_turn(self, text: str) -> None:
        if self._busy:
            self.append_activity("note", "Still working — wait or press Ctrl+C to cancel.")
            return
        thread = threading.Thread(
            target=self.run_turn, args=(text,), name="seedcode-turn", daemon=True
        )
        thread.start()

    # --- application --------------------------------------------------------
    def _leave(self, result: tuple[str, str | None]) -> None:
        app = self._app
        if app is not None:
            app.exit(result=result)

    def _invalidate(self) -> None:
        app = self._app
        if app is None:
            return
        try:
            loop = getattr(app, "loop", None)
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(app.invalidate)
            else:
                app.invalidate()
        except Exception:
            pass

    def _before_render(self, app: Application) -> None:
        """Re-fit every region to the live terminal size before each frame."""
        try:
            size = app.output.get_size()
        except Exception:
            return
        width = max(20, size.columns)
        self._width = width
        if getattr(self.console, "_width", None) != width:
            self.console._width = width  # type: ignore[attr-defined]
        lines = header_lines(self.state, width)
        if self._header_window is not None:
            self._header_window.height = Dimension.exact(len(lines))
        if self._input_window is not None:
            self._input_window.height = Dimension.exact(self._input_height())
        if self._permission_window is not None:
            self._permission_window.height = Dimension.exact(self._permission_height())

    # --- fragments ----------------------------------------------------------
    def _header_fragments(self) -> list[tuple[str, str]]:
        lines = header_lines(self.state, self._width)
        flat: list[tuple[str, str]] = []
        for index, line in enumerate(lines):
            if index:
                flat.append(("", "\n"))
            flat.extend(line)
        return flat

    def _composer_title(self) -> str:
        """The frame's top-border label (a pending confirmation reuses it)."""
        prompt = self._confirm_prompt
        if prompt is not None:
            return f" {prompt[0]} "
        return " Message "

    def _composer_bottom(self) -> list[tuple[str, str]]:
        """The single hint line under the bounded composer.

        The box itself is drawn by :class:`Frame`; this line only carries live
        context — a pending permission prompt, the cancel hint while a turn
        runs, the submit/newline keys — and, only while the user is reading
        history, how much new output arrived below the viewport.
        """
        from .theme import active_palette

        palette = active_palette()
        if self._confirm_prompt is not None:
            hint = "  Enter allow  \u00b7  a allow session  \u00b7  d deny  \u00b7  Esc cancel"
        elif self._busy:
            hint = "  Ctrl+C cancel"
        else:
            hint = "  Enter \u21b5 send  \u00b7  Shift+Enter newline"
        parts: list[tuple[str, str]] = []
        if not self._follow and self._unseen > 0:
            parts.append((f"fg:{palette.warning}", f"  \u2193 {self._unseen} new"))
        parts.append((f"fg:{palette.dim}", hint))
        return parts

    def _build_app(self) -> Application:
        kb = KeyBindings()

        @kb.add("enter")
        def _(_event) -> None:
            # One submission path: Enter runs the buffer's accept handler.
            self._input.validate_and_handle()

        # Shift+Enter where the terminal reports it distinctly, plus the
        # universally available Ctrl+J / Alt+Enter newline bindings.
        @kb.add("escape", "enter")
        @kb.add("c-j")
        def _(_event) -> None:
            self._input.insert_text("\n")

        # Tab / Shift+Tab remain focus navigations (harmless with a single
        # focusable field, and they preserve the historical bindings).
        @kb.add("tab")
        def _(event) -> None:
            event.app.layout.focus_next()

        @kb.add("s-tab")
        def _(event) -> None:
            event.app.layout.focus_previous()

        @kb.add("up")
        def _(_event) -> None:
            # Inside a multiline message Up/Down move the cursor; only at the
            # top/bottom edge do they walk the input history.
            if self._input.document.cursor_position_row > 0:
                self._input.cursor_up()
            else:
                self._history_move(-1)

        @kb.add("down")
        def _(_event) -> None:
            last_row = self._input.document.line_count - 1
            if self._input.document.cursor_position_row < last_row:
                self._input.cursor_down()
            else:
                self._history_move(1)

        @kb.add("pageup")
        def _(_event) -> None:
            self.scroll_by(-10)

        @kb.add("pagedown")
        def _(_event) -> None:
            self.scroll_by(10)

        @kb.add("c-home")
        def _(_event) -> None:
            self._follow = False
            self._scroll = 0
            self._invalidate()

        @kb.add("c-end")
        def _(_event) -> None:
            self.scroll_to_bottom()

        @kb.add("c-k")
        def _(_event) -> None:
            self._leave(("key", "palette"))

        @kb.add("c-p")
        def _(_event) -> None:
            self._leave(("key", "files"))

        @kb.add("c-r")
        def _(_event) -> None:
            self._leave(("key", "history"))

        @kb.add("c-_")
        def _(_event) -> None:
            self._leave(("key", "shortcuts"))

        @kb.add("c-l")
        def _(event) -> None:
            self.buffer.clear()
            self._invalidate()

        @kb.add("c-c")
        def _(_event) -> None:
            if self._deny_confirm():
                return
            if self._busy:
                self.request_cancel()
            else:
                self._input.text = ""
                self._invalidate()

        # NOTE: deliberately not eager. An eager bare-Escape binding fires the
        # instant ESC arrives and therefore pre-empts the escape-prefixed
        # sequences above — which would silently break Alt+Enter
        # (``escape,enter`` → newline) on terminals that send it that way.
        @kb.add("escape")
        def _(_event) -> None:
            if self._deny_confirm():
                return
            if self._busy:
                self.request_cancel()
            elif self._input.text:
                self._input.text = ""

        @kb.add("c-d")
        def _(_event) -> None:
            if not self._busy:
                self._leave(("exit", None))

        content_control = _ContentControl(lambda w, h: self._slice(w, h), self._content_mouse)
        self._content_window = Window(
            content_control,
            height=Dimension(weight=1),
            wrap_lines=False,
        )
        self._header_window = Window(
            FormattedTextControl(self._header_fragments, focusable=False),
            height=Dimension.exact(len(header_lines(self.state, self._width))),
            dont_extend_height=True,
        )
        self._input_window = Window(
            BufferControl(buffer=self._input),
            height=Dimension.exact(self._input_height()),
            dont_extend_height=True,
            wrap_lines=True,
        )
        # The permission panel is a persistent region (height 0 unless a
        # request is pending), never a nested application or a blocking read.
        self._permission_window = Window(
            FormattedTextControl(self._permission_fragments, focusable=False),
            height=Dimension.exact(self._permission_height()),
            dont_extend_height=True,
            wrap_lines=False,
        )
        # A real bounded editor: Frame draws the ╭─ Message ─╮ box around the
        # BufferControl, so the border *is* the visual boundary. The chevron
        # lives in its own fixed-width column (its width is the wrapped-line
        # indent) and the right padding keeps text off the border.
        composer_body = VSplit(
            [
                Window(
                    FormattedTextControl(self._prompt_fragments, focusable=False),
                    width=Dimension.exact(len(_COMPOSER_PROMPT)),
                    dont_extend_width=True,
                ),
                self._input_window,
                Window(width=Dimension.exact(_COMPOSER_PAD)),
            ]
        )
        composer = Frame(
            composer_body,
            title=lambda: self._composer_title().strip(),
            style="class:composer",
        )
        root = HSplit(
            [
                self._header_window,
                self._content_window,
                self._permission_window,
                composer,
                Window(
                    FormattedTextControl(self._composer_bottom, focusable=False),
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                ),
            ]
        )
        return Application(
            layout=Layout(root, focused_element=self._input_window),
            key_bindings=kb,
            style=DynamicStyle(lambda: pt_style()),
            full_screen=True,
            mouse_support=True,
            before_render=self._before_render,
            erase_when_done=False,
            input=self._input_device,
            output=self._output_device,
        )

    def _content_mouse(self, mouse_event) -> Any:
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.scroll_by(-3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.scroll_by(3)
            return None
        return NotImplemented

    def run_once(self) -> tuple[str, str | None]:
        """Run the application until a command, a key action, or an exit.

        Messages and mode switches do not end the run: they execute while the
        application keeps the screen. The Application, Layout and every region
        are built **once** and reused across re-entries, so no terminal state is
        destroyed and recreated (the root of the old mode-switch freeze).
        """
        if self._app is None:
            self._app = self._build_app()
        try:
            result = self._app.run()
        except (KeyboardInterrupt, EOFError):
            result = ("exit", None)
        except Exception:
            result = ("exit", None)
        if not isinstance(result, tuple) or len(result) != 2:
            return ("exit", None)
        kind, payload = result
        if kind == "exit":
            self._app = None  # the session is over; release the application
        return (str(kind), payload if isinstance(payload, str) else None)

    def shutdown(self) -> None:
        """Stop background UI work and release the application (called once)."""
        self.stop_thinking()
        self._cancel.set()
        self._app = None

    def banner_text(self) -> None:
        """The opening content: just how to drive an already-branded session.

        The header already carries the identity (version, logo, tagline), so the
        scrollback only adds the input hint — nothing is printed twice.
        """
        from .reference import INPUT_HINT

        self.console.print()
        self.console.print(safe_text(INPUT_HINT), style=_rich("dim"))


def _palette():
    from .theme import active_palette

    return active_palette()


def _rich(role: str, *, bold: bool = False) -> str:
    """A Rich style for a palette role (Rich uses ``"bold #rrggbb"`` syntax)."""
    color = getattr(_palette(), role, _palette().text)
    return f"bold {color}" if bold else color


def _pt(role: str, *, bold: bool = False) -> str:
    """A prompt_toolkit inline style for a palette role (``fg:#rrggbb``)."""
    color = getattr(_palette(), role, _palette().text)
    return f"bold fg:{color}" if bold else f"fg:{color}"


def _assistant_lines(lines: list[StyledLine]) -> list[StyledLine]:
    """Attribute rendered assistant output with the ``AI >`` prefix.

    Only the first line carries the prefix: wrapped and continued lines keep
    their own left margin, so rendered markdown (lists, code blocks) is not
    re-indented by the UI.
    """
    body = list(lines)
    while body and not "".join(text for _, text in body[0]).strip():
        body.pop(0)
    if not body:
        return [[("", "")]]
    first: StyledLine = [(_pt("primary", bold=True), _AI_PREFIX)] + list(body[0])
    return [first] + [list(line) for line in body[1:]]


def _markdown_lines(text: str, width: int) -> list[StyledLine]:
    """Render assistant markdown to styled lines through a recording console."""
    from rich.markdown import Markdown

    from .content import parse_ansi, trim_line

    recorder = Console(
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
        width=max(20, width),
        theme=rich_theme(),
        highlight=False,
        record=True,
    )
    try:
        recorder.print(Markdown(safe_text(text), code_theme="ansi_dark"))
        rendered = recorder.export_text(styles=True)
    except Exception:
        rendered = safe_text(text)
    return [trim_line(line) for line in parse_ansi(rendered)]


# --- the UI adapter -----------------------------------------------------------
class TuiUI:
    """A :class:`~seedcode.ui.UI`-compatible facade backed by the TUI.

    It inherits the full UI surface so every command, engine and presenter keeps
    working, but rendering goes to the TUI's sink console and the live displays
    (spinner, streaming, task flow) are replaced by the persistent regions.
    """

    is_tui = True

    def __init__(self, tui: ChatTUI) -> None:
        self.tui = tui
        self.state = tui.state
        self.console = tui.console
        self._theme_pushed = False
        self._live = None
        self._ok_mark, self._err_mark = "\u2714", "\u2716"

    # --- identity/theme -----------------------------------------------------
    def apply_theme(self, name: str) -> None:
        from .theme import rich_theme as _rich_theme, set_active_theme

        set_active_theme(name)
        if self._theme_pushed:
            try:
                self.console.pop_theme()
            except Exception:
                pass
        self.console.push_theme(_rich_theme(name))
        self._theme_pushed = True
        self.tui._invalidate()

    # --- primitives (mirroring UI) -----------------------------------------
    def print(self, *args, **kwargs) -> None:
        # Cooperative cancellation: the turn calls back into the UI constantly,
        # so checking here stops a cancelled turn almost immediately.
        self.tui.check_cancel()
        try:
            self.console.print(*args, **kwargs)
        except Exception:
            pass

    def blank(self) -> None:
        self.print()

    def _emit(self, *args, **kwargs) -> None:
        self.print(*args, **kwargs)

    def info(self, message: str) -> None:
        self.print(safe_text(message))

    def dim(self, message: str) -> None:
        self.print(safe_text(message), style=_rich("dim"))

    def success(self, message: str) -> None:
        self.print(f"{self._ok_mark} {safe_text(message)}", style=_rich("success"))

    def warning(self, message: str) -> None:
        self.print(f"! {safe_text(message)}", style=_rich("warning"))

    def error(self, message: str) -> None:
        self.print(f"{self._err_mark} {safe_text(message)}", style=_rich("error"))

    def panel(self, body, title: str | None = None) -> None:
        from rich.panel import Panel

        self.print(
            Panel(body, title=title, border_style="seed.primary", title_align="left")
        )

    # --- live displays ------------------------------------------------------
    def register_live(self, live) -> None:
        self._live = live

    def unregister_live(self, live) -> None:
        if self._live is live:
            self._live = None

    def banner(self, config: Any) -> None:
        # The header *is* the banner now; the dashboard is never reprinted.
        self.tui.sync_from_config(config)

    def statusbar(self, config: Any) -> None:
        self.tui.sync_from_config(config)

    @contextmanager
    def thinking(self, label: str = "Thinking") -> Iterator[None]:
        """Animate ``AI > ◌ Thinking…`` while a real operation is in flight.

        The indicator is removed on exit — including when the block wrapped a
        command that ran with the prompt_toolkit application stepped aside — so
        it can never be left behind on screen. Streamed output takes over the
        live block immediately, and the turn's own ``finally`` is a no-op by
        then.
        """
        self.tui.check_cancel()
        self.tui.start_thinking(label)
        self.tui.state.set_status(Status.THINKING, label.lower())
        self.tui._invalidate()
        try:
            yield
        except BaseException:
            self.tui.stop_thinking()
            raise
        else:
            self.tui.stop_thinking()
            self.tui.check_cancel()

    @contextmanager
    def streaming(self) -> Iterator[TuiStreamRenderer]:
        # Real output supersedes the thinking indicator immediately.
        self.tui.stop_thinking()
        renderer = TuiStreamRenderer(self.tui)
        self.tui.state.set_status(Status.WORKING, "writing a reply")
        self.tui._invalidate()
        try:
            yield renderer
        except KeyboardInterrupt:
            renderer.flush()
            self.tui.finish_stream()
            raise
        else:
            renderer.flush()
            self.tui.finish_stream()

    # --- confirmations ------------------------------------------------------
    def _confirm(self, title: str, category_label: str, description: str) -> str:
        return self.tui.confirm(title, category_label, description)

    def confirm_desktop(self, category_label: str, description: str) -> str:
        return self._confirm("Desktop Control", category_label, description)

    def confirm_tool_action(self, category_label: str, description: str) -> str:
        return self._confirm("Agent Action", category_label, description)
