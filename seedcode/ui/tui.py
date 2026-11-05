"""The persistent Seed Code terminal interface (v9.1.0).

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
* a **fixed composer** — a real, bounded multiline editor with a full-width
    dark field, green left rail, model/agent metadata and a compact status footer.
    Long lines wrap inside the field, taller content scrolls internally, and
    typed text can never leak outside the composer. ``Enter`` sends,
  ``Shift+Enter`` inserts a newline, ``↑``/``↓`` move the cursor inside a
  multiline message (and walk the input history at the edges), and ``Esc``
  clears.

The single source of session truth is the **header** dashboard: provider,
model, mode and status each appear in exactly one place — there is no second
toolbar repeating them, and no separate Submit button competing with Enter.

The compact activity area shows real request/tool lifecycle events above the
composer. While a provider is silent it reports an honest waiting state and
elapsed time; private reasoning is never rendered as user-facing activity.

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
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl,
    UIContent,
    UIControl,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import DynamicStyle
from rich.console import Console

from ..utils.text import safe_text
from .content import ContentBuffer, StyledLine, trim_line
from .header import header_lines
from .intro import PLACEHOLDER
from .state import AppState, Status, status_fragment
from .theme import active_palette, gradient_colors, mix, pt_style, rich_theme, shimmer_fragments

__all__ = ["ChatTUI", "TuiUI", "TuiStreamRenderer"]

#: Repaint the live block at most this often while streaming (never per token).
_STREAM_INTERVAL_S = 1 / 15
#: The composer always shows at least this many rows (room for a wrapped line),
#: grows with the message, and starts scrolling internally past the maximum.
_MIN_INPUT_LINES = 2
_MAX_INPUT_LINES = 6
#: Rows of the bounded permission panel (shown only while a request waits).
_PERMISSION_HEIGHT = 7
#: The slash-command suggestion list shows at most this many rows; typing
#: further narrows the list instead of growing it.
_MAX_AUTOCOMPLETE_ROWS = 8
_HINT_ROWS = 1
#: The conversation is never allowed to vanish: this many rows are reserved for
#: it (and therefore taken from the header) before anything else gives way.
_MIN_CONVERSATION_ROWS = 1
#: A terminal this short cannot show the three regions at all; the layout still
#: renders (an empty conversation and the composer rather than a blank screen).
_MIN_TERMINAL_ROWS = 3

#: Every conversation line starts with this margin (the OpenCode-style
#: two-cell gutter). User blocks additionally paint their own surface rail.
_ACTIVITY_INDENT = "  "

#: Assistant answers carry a two-cell vertical gradient rail instead of a flat
#: margin. The trailing space keeps the first word off the rail.
_ASSISTANT_BAR = "\u258c\u258c "
_ASSISTANT_BAR_WIDTH = 3

#: Spinner cadence for the one live activity line ({\u280b, \u2819, ...}).
_THINK_INTERVAL_S = 0.09
_SPINNER_FRAMES = ("\u280b", "\u2819", "\u2839", "\u2838", "\u283c", "\u2834", "\u2826", "\u2827", "\u2807", "\u280f")


def _agentic_mode(mode: str) -> bool:
    """Whether ``mode`` is Agent Mode (the only mode that acts on the project)."""
    return str(mode).startswith("Agent")


#: Commands dispatched in place inside the live application (no exit, no
#: rebuild). v9.2.0 has no mode switches any more; /workspace is included so
#: changing the active project never tears down the session either.
_MODE_COMMANDS = frozenset({"workspace", "permission", "status"})


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


class _NullStream:
    """A file-like sink that discards everything written to it.

    The streamed-markdown recorder has to *record* a rendered answer, never
    print it. Its console therefore writes here instead of to ``sys.stdout``:
    the TUI's own console is the only thing allowed to touch the screen. Before
    this, every streamed flush also wrote the raw ANSI rendering to stdout —
    straight underneath the prompt_toolkit full-screen interface, which is
    exactly the whole-interface flicker (and the duplicated answer) users saw.
    """

    encoding = "utf-8"
    errors = "replace"

    def write(self, text: str) -> int:
        return len(text) if text else 0

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


class _PlaceholderProcessor(Processor):
    """Show the home placeholder only while the composer buffer is empty."""

    def apply_transformation(self, transformation_input):
        if transformation_input.document.text:
            return Transformation(transformation_input.fragments)
        return Transformation(
            [
                ("class:placeholder.cursor", "\u25ae"),
                ("class:placeholder", " " + PLACEHOLDER),
            ]
        )


class _ContentControl(UIControl):
    """A non-focusable control that renders the buffer's visible slice."""

    #: v10.2.0: the conversation uses the complete terminal width — no reserved
    #: sidebar column and no decorative left margin.
    LEFT_PAD = 0

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
        """Render the visible slice as a :class:`UIContent` directly.

        Deliberately *not* by building a ``FormattedTextControl`` per frame: a
        streaming answer repaints fifteen times a second, and allocating a new
        control (with its own fragment processing) on every frame was needless
        churn in the one region that already updates continuously.
        """
        pad = max(0, min(self.LEFT_PAD, width - 1))
        try:
            lines = self._provider(width - pad, height)
        except Exception:
            lines = [[("", "(display error)")]]
        # Conversation-region padding: the provider slices to the inner width
        # and this control prepends the left pad, so a message is never flush
        # against (or clipped by) the terminal's left edge.
        prefix = [("", " " * pad)]
        rendered: list[list[tuple[str, str]]] = [prefix + line for line in lines]
        return UIContent(
            get_line=lambda index: rendered[index],
            line_count=len(rendered),
            show_cursor=False,
        )


class _AutocompleteControl(UIControl):
    """A non-focusable, clickable slash-command suggestion list.

    ``provider`` renders the visible rows; a mouse click on a row calls
    ``on_pick(row)`` — the row number is the row of the window, which is
    exactly one displayed suggestion.
    """

    def __init__(
        self,
        provider: Callable[[int, int], list[StyledLine]],
        on_pick: Callable[[int], None],
    ) -> None:
        self._provider = provider
        self._on_pick = on_pick

    def is_focusable(self) -> bool:
        return False

    def is_scrollable(self) -> bool:
        return False

    def create_content(self, width: int, height: int):
        try:
            rows = self._provider(width, height)
        except Exception:
            rows = [[("", "")]]
        if not rows:
            rows = [[("", "")]]
        return UIContent(
            get_line=lambda index: rows[index],
            line_count=len(rows),
            show_cursor=False,
        )

    def mouse_handler(self, mouse_event):
        if mouse_event.event_type == MouseEventType.MOUSE_CLICK:
            self._on_pick(mouse_event.position.y)
            return None
        if mouse_event.event_type == MouseEventType.MOUSE_MOVE:
            return None  # hover selection is noise in a one-row-per-item list
        return NotImplemented


class _PlanControl(UIControl):
    """The right-side TODO panel (v9.3.1): dynamic width, real hierarchical state.

    ``provider(width, height)`` renders the visible rows from live state; the
    control reports its own preferred width, so the panel sizes to the plan
    (clipped) and *collapses to nothing* only when there is no plan or the
    terminal is too narrow to give the conversation a usable column at all.
    """

    #: Limits: hard-clip any title, hide entirely under this terminal width.
    MAX_WIDTH = 34
    MIN_ROOM = 48
    MAX_TITLE = 30

    def __init__(
        self,
        provider: Callable[[int, int], list[StyledLine]],
        width_fn: Callable[[], int],
        rows_fn: Callable[[], list[tuple[str, str]]],
    ) -> None:
        self._provider = provider
        self._width_fn = width_fn
        self._rows_fn = rows_fn

    def is_focusable(self) -> bool:
        return False

    def is_scrollable(self) -> bool:
        return False

    def preferred_width(self, max_available_width: int) -> int | None:
        return calculated_plan_width(self._rows_fn(), self._width_fn())

    def create_content(self, width: int, height: int):
        try:
            fragments = self._provider(width, height)
        except Exception:
            fragments = [[("", "")]]
        if not fragments:
            fragments = [[("", "")]]
        return UIContent(
            get_line=lambda index: fragments[index],
            line_count=len(fragments),
            show_cursor=False,
        )

    def mouse_handler(self, mouse_event):
        return NotImplemented


def calculated_plan_width(rows, terminal_width: int) -> int:
    """The panel width: title-bound, terminal-limited, 0 when it must hide.

    The panel keeps a real column on a narrow terminal (v9.3.1) instead of
    silently disappearing: it only hides below :data:`_PlanControl.MIN_ROOM`
    columns, where a split would leave the conversation unusable.
    """
    if not rows or terminal_width < _PlanControl.MIN_ROOM:
        return 0
    longest = 0
    for row in rows:
        try:
            title = str(row[1] or "")
            depth = int(row[2] or 0)
        except (IndexError, TypeError, ValueError):
            title, depth = str(row or ""), 0
        longest = max(longest, len(title) + 2 * depth)
    clipped = min(longest, _PlanControl.MAX_TITLE)
    # glyph + arrows + padding, and never more than a third of the screen.
    return max(8, min(_PlanControl.MAX_WIDTH, clipped + 7, terminal_width // 3))


class _ThinkingControl(UIControl):
    """The bounded THINKING panel (v9.2.0): only what the model exposes.

    ``provider(width, height)`` renders the visible slice of the model's own
    reasoning stream. The control reports its preferred height, so the panel
    reserves roughly the spec's 25-30% of the terminal while thinking flows
    and collapses to nothing when empty or when the user collapsed it.
    """

    def __init__(
        self,
        provider: Callable[[int, int], list[StyledLine]],
        height_fn: Callable[[], int],
    ) -> None:
        self._provider = provider
        self._height_fn = height_fn

    def is_focusable(self) -> bool:
        return False

    def is_scrollable(self) -> bool:
        return False

    def preferred_height(
        self,
        width: int,
        max_available_height: int,
        wrap_lines: bool = False,
        get_line_prefix=None,
    ) -> int | None:
        return self._height_fn()

    def create_content(self, width: int, height: int):
        try:
            fragments = self._provider(width, height)
        except Exception:
            fragments = [[("", "")]]
        if not fragments:
            fragments = [[("", "")]]
        return UIContent(
            get_line=lambda index: fragments[index],
            line_count=len(fragments),
            show_cursor=False,
        )

    def mouse_handler(self, mouse_event):
        return NotImplemented


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

    def reset(self) -> None:
        """Drop the preview (the streamed text was intermediate prose)."""
        self._buffer = ""
        self._last = 0.0
        self._tui.set_stream("", force=True)

    def reflow(self) -> None:
        """Re-render the buffered answer at the current terminal width.

        Called when the terminal is resized mid-stream so the visible answer
        re-wraps to the new size instead of keeping the old wrapping.
        """
        if self._buffer:
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
        height: int = 24,
        input_device: Any = None,
        output_device: Any = None,
    ) -> None:
        self.state = state
        self._input_device = input_device
        self._output_device = output_device
        self._width = max(20, int(width))
        #: The live terminal *height*, tracked so every region can be sized from
        #: the real screen on each frame instead of from a fixed assumption.
        self._rows = max(_MIN_TERMINAL_ROWS, int(height))
        #: The row allocation for the live size, recomputed (never re-built) on
        #: each frame by :meth:`_reallocate_rows`.
        self._header_rows = 0
        self._input_rows = _MIN_INPUT_LINES
        self._composer_top_rows = 0
        self._composer_meta_rows = 0
        self._composer_rule_rows = 0
        self._workspace_rows = 0
        self._status_rows = 1
        self._permission_rows = 0
        self._thinking_rows = 0
        self._hint_rows = _HINT_ROWS
        #: The thread running the prompt_toolkit event loop. A permission ask
        #: arriving on this thread could never be answered (the event loop is
        #: what delivers the answer), so it is refused instead of deadlocking.
        self._ui_thread: int | None = None
        #: The live stream block, so a resize can re-wrap the answer in place.
        self._stream: "TuiStreamRenderer | None" = None
        #: The last file change (path, diff), expandable with Ctrl+E.
        self._last_change: tuple[str, str] | None = None
        #: The last terminal command's real result, expandable with Ctrl+T.
        self._last_command: dict | None = None
        #: Slash-command autocomplete: the entries currently suggested above
        #: the composer, the highlighted row, and the row budget. The catalog
        #: is loaded lazily from the live dispatch registry
        #: (``seedcode.commands``), so the suggestions are always the commands
        #: the backend actually dispatches.
        self._auto_entries: list[tuple[str, str]] = []
        self._auto_index = 0
        self._auto_rows = 0
        self._command_catalog: list[tuple[str, str]] | None = None
        #: Cancel a mode's background initialization (wired by the controller).
        self.on_cancel_init: Callable[[], None] | None = None
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
            # Rich output is parsed into the screen buffer; giving the console
            # the canvas colour means every rendered span (including padding)
            # carries it instead of falling back to the terminal profile.
            style=f"on {active_palette().bg}",
            highlight=False,
        )
        self._input = Buffer(
            multiline=True,
            accept_handler=self._accept,
            on_text_changed=self._on_input_changed,
        )
        self._app: Application | None = None
        self._header_window: Window | None = None
        self._input_window: Window | None = None
        self._content_window: Window | None = None
        self._hint_window: Window | None = None
        self._workspace_window: Window | None = None
        # --- v9.2.0: the right-side TASK PLAN + bounded THINKING panels -----
        self._plan_window: Window | None = None
        self._thinking_window: Window | None = None
        self._scroll = 0
        self._follow = True
        #: Lines below the viewport while the user is scrolled up (a hint only).
        self._unseen = 0
        self._history: list[str] = []
        self._history_index = 0
        self._draft = ""
        self._busy = False
        #: True once the completed reasoning was copied into the scrollback, so
        #: a later resize/turn never duplicates the block.
        self._reasoning_committed = False
        #: The one live action line (spinner -> check) shown above the composer.
        #: It is replaced in place, never appended, so a tool call has one line.
        self._live_action: tuple[str, str] | None = None
        self._turn_started = 0.0
        #: Dynamic side padding turns the composer into a centered home card on
        #: the welcome screen, then releases it to full width in chat.
        self._composer_pad = 0
        #: True only after :meth:`banner_text` — the centred intro state. It is
        #: cleared by the first user turn, so the intro can never reappear over
        #: a live conversation (and tests that build a TUI directly see the
        #: plain conversation area, exactly as before).
        self._intro_visible = False
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
        # When the indicator started (for the live elapsed-seconds counter).
        self._think_started = 0.0
        self._reallocate_rows()  # a sane first frame before the real size lands
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
        """Echo the user's turn as a surface block with a vertical gradient rail.

        There is no ``You >`` prefix: the block itself is the attribution. One
        blank surface row pads the message above and below, and the rail moves
        from the brand green to the accent blue down the block.
        """
        from rich.text import Text

        palette = active_palette()
        self._intro_visible = False
        lines = safe_text(text).splitlines() or [""]
        rows = [""] + lines + [""]
        colours = gradient_colors(palette.brand_start, palette.brand_end, len(rows))
        width = max(20, self._width)
        self.console.print()
        for value, colour in zip(rows, colours):
            line = Text()
            line.append("\u258c", style=f"{colour} on {palette.surface}")
            line.append(" ", style=f"on {palette.surface}")
            line.append(value, style=f"{palette.text} on {palette.surface}")
            used = 3 + len(value)
            if used < width:
                line.append(" " * (width - used), style=f"on {palette.surface}")
            self.console.print(line, overflow="fold", highlight=False)
        self.console.print()

    def append_activity(self, kind: str, text: str, *, legacy: bool = False) -> None:
        """Update the one live operation line instead of appending duplicates.

        Real backend events drive this: ``action`` starts a spinner line,
        ``success`` turns it into a check and ``error`` becomes a red result.
        The line is replaced in place, so a tool call can never leave both a
        "started" and a "finished" row behind. Informational notes are the one
        exception: they are history, not a progress step, so they are committed.
        """
        safe_activity = safe_text(text).strip()
        if not safe_activity:
            return
        self.state.add_activity(kind, safe_activity)
        if kind in ("action", "success"):
            self._live_action = (kind, safe_activity)
            self._invalidate()
            return
        if kind == "error":
            self._live_action = None
            self._commit_activity("error", safe_activity)
            self._invalidate()
            return
        self._commit_activity("note", safe_activity)

    def _commit_activity(self, kind: str, text: str) -> None:
        """Commit one compact, real activity line to the scrollback."""
        from rich.text import Text

        palette = active_palette()
        marks = {
            "action": ("\u2699", palette.info),
            "success": ("\u2713", palette.success),
            "error": ("\u2715", palette.error),
            "note": ("\u2139", palette.muted),
        }
        mark, colour = marks.get(kind, marks["note"])
        line = Text()
        if kind == "error":
            line.append("  \u258c ", style=palette.error)
            line.append("Error  ", style=f"bold {palette.error}")
            line.append(text, style=palette.muted)
        else:
            line.append(_ACTIVITY_INDENT, style=palette.muted)
            line.append(f"{mark} ", style=colour)
            line.append(text, style=colour if kind != "note" else palette.muted)
        self.console.print(line, highlight=False, overflow="fold")

    def append_change(self, event: dict) -> None:
        """Render a real file change: summary, line ranges and a diff preview."""
        from rich.text import Text

        path = safe_text(str(event.get("path") or "(unknown)"))
        diff = safe_text(str(event.get("diff") or ""))
        kind = str(event.get("type") or "")
        added = int(event.get("lines_added") or 0)
        removed = int(event.get("lines_removed") or 0)
        start = int(event.get("line") or 0)
        end = int(event.get("line_end") or start)
        if kind == "file_created":
            verb = "Created"
        elif kind == "file_deleted":
            verb = "Deleted"
        else:
            verb = "Updated"
        summary = f"{verb} {path} — +{added}/-{removed} lines"
        if start:
            summary += f" (lines {start}–{end})" if end and end != start else f" (line {start})"
        self._last_change = (path, diff)
        self._commit_activity("success" if kind != "file_deleted" else "note", summary)
        self._print_diff(diff, limit=12, indent="    ")
        if diff:
            self.console.print(
                Text("      Ctrl+E expands the last diff", style="seed.dim"),
                highlight=False,
            )

    def _print_diff(self, diff: str, *, limit: int | None, indent: str = "") -> None:
        """Print unified-diff body lines with add/remove colouring."""
        from rich.text import Text

        lines = [
            line
            for line in (diff or "").splitlines()
            if not line.startswith(("---", "+++"))
        ]
        shown = lines if limit is None else lines[:limit]
        for line in shown:
            style = (
                "seed.success"
                if line.startswith("+")
                else "seed.error"
                if line.startswith("-")
                else "seed.dim"
            )
            self.console.print(Text(f"{indent}{line}", style=style), highlight=False)
        if limit is not None and len(lines) > limit:
            self.console.print(
                Text(
                    f"{indent}... {len(lines) - limit} more diff line(s)",
                    style="seed.dim",
                ),
                highlight=False,
            )

    def show_last_change(self) -> None:
        """Expand the last file diff in full (Ctrl+E)."""
        if self._last_change is None:
            self.append_activity("note", "No file change to expand yet.")
            return
        path, diff = self._last_change
        self._commit_activity("note", f"Full diff for {path}")
        self._print_diff(diff, limit=None)
        self._invalidate()

    # --- terminal inspection (Ctrl+T) -----------------------------------
    def record_command(self, command: str, ok: bool, output: str) -> None:
        """Store one terminal command's real result for later inspection."""
        self._last_command = {
            "command": safe_text(str(command or "")),
            "ok": bool(ok),
            "output": safe_text(str(output or "")),
            "cwd": safe_text(str(getattr(self.state, "workspace", "") or "")),
        }

    def show_last_command(self) -> None:
        """Expand the last terminal command in full (Ctrl+T)."""
        from rich.text import Text

        record = self._last_command
        if record is None:
            self._commit_activity("note", "No command has run yet (Ctrl+T).")
            return
        ok = bool(record.get("ok"))
        command = str(record.get("command") or "")
        cwd = str(record.get("cwd") or "(workspace)")
        body = str(record.get("output") or "")
        # The tool's own status line ("DONE" / "FAILED (exit N)") is
        # real output; it is rendered as the status header, the rest as body.
        lines = body.splitlines() or [""]
        status_line = lines[0]
        self._commit_activity("note", f"$ {command}   (cwd: {cwd})")
        self._commit_activity(
            "success" if ok else "error",
            status_line.split(" ", 1)[-1] if status_line else "finished",
        )
        shown = lines[1:301]
        for line in shown:
            self.console.print(
                Text(f"    {line}", style="seed.dim"), highlight=False
            )
        if len(lines) - 1 > len(shown):
            self.console.print(
                Text(
                    f"    ... {len(lines) - 1 - len(shown)} more line(s)",
                    style="seed.dim",
                ),
                highlight=False,
            )
        self._invalidate()

    # --- final execution report ----------------------------------------------
    def append_report(self, report: dict) -> None:
        """The final report for the turn, from this turn's real events only.

        Every number here was observed by the backend (file operations, command
        results, tests, research). A section with nothing to report is omitted
        rather than filled in with a placeholder.
        """
        from rich.text import Text

        from .theme import active_palette

        palette = active_palette()
        heading = "Execution Report"
        state = str(report.get("state") or "completed")
        mark, mark_style = (
            ("\u2713", "seed.success")
            if state == "completed"
            else ("\u2715", "seed.error")
        )
        self.console.print(
            Text(f"\u2500 {heading} " + "\u2500" * 4, style=mark_style),
            highlight=False,
        )

        def section(text: str) -> None:
            self.console.print(Text(text, style=f"fg:{palette.dim}"), highlight=False)

        def paths(label: str, items: list[str]) -> None:
            items = sorted(dict.fromkeys(items))
            if not items:
                return
            shown = ", ".join(items[:6])
            more = len(items) - 6
            section(f"  {label}: {len(items)} — {shown}{f' (+{more})' if more > 0 else ''}")

        paths("Files created", list(report.get("created") or []))
        paths("Files modified", list(report.get("modified") or []))
        paths("Files deleted", list(report.get("deleted") or []))

        commands: list[tuple[str, bool]] = list(report.get("commands") or [])
        if commands:
            ok_count = sum(1 for _cmd, ok in commands if ok)
            last_cmd, last_ok = commands[-1]
            section(
                f"  Commands: {ok_count}/{len(commands)} ok — "
                f"last: {last_cmd[:80]} {'(ok)' if last_ok else '(FAILED)'}"
            )
        tests = report.get("tests")
        if tests:
            ok_count = sum(1 for ok in tests if ok)
            section(f"  Tests: {ok_count}/{len(tests)} run(s) passed")
        research = report.get("research")
        if research:
            section(f"  Research: {len(research)} source(s) consulted")
        errors: list[str] = list(report.get("errors") or [])
        if errors:
            for error in errors[-3:]:
                self.console.print(
                    Text(f"  \u2715 {error[:160]}", style="seed.error"),
                    highlight=False,
                )
        self.console.print(highlight=False)

    def set_stream(self, text: str, *, force: bool = False) -> None:
        """Render the streamed markdown into the live block (throttled)."""
        if not text and not force:
            return
        if not text:
            self.buffer.clear_live()
            return
        self.buffer.set_live(
            _assistant_lines(
                _markdown_lines(text, max(20, self._width - _ASSISTANT_BAR_WIDTH))
            )
        )

    def finish_stream(self) -> None:
        stream = self._stream
        text = stream.text if stream is not None else ""
        self.buffer.commit_live()
        self._live_action = None
        if text.strip():
            self._assistant_footer()
        self.console.print()
        self._invalidate()

    def _assistant_footer(self) -> None:
        """The quiet ``■ Build · model · duration`` line under an answer."""
        from rich.text import Text

        palette = active_palette()
        elapsed = max(0.0, time.monotonic() - self._turn_started) if self._turn_started else 0.0
        duration = f"{elapsed:.1f}s" if elapsed < 60 else f"{elapsed / 60:.1f}m"
        line = Text()
        line.append("  \u25a0 ", style=palette.primary)
        line.append(self.state.agent_state or "Build", style=f"bold {palette.info}")
        line.append("  \u00b7  ", style=palette.muted)
        line.append(self.state.model or "model", style=palette.text)
        line.append("  \u00b7  ", style=palette.muted)
        line.append(duration, style=palette.muted)
        self.console.print(line, highlight=False)

    def _render_thinking(self) -> None:
        self._invalidate()

    def start_thinking(self, label: str = "Waiting for model response") -> None:
        """Start the elapsed wait clock used by the live activity panel."""
        with self._think_lock:
            self._think_label = (label or "").strip() or "Thinking"
            self._think_index = 0
            self._think_started = time.monotonic()
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
            self._think_started = 0.0
            stop = self._think_stop
            self._think_stop = None
            self._think_thread = None
        if stop is not None:
            stop.set()
        # Preserve completed reasoning in the conversation instead of erasing
        # it: the live strip is transient, the history block is not.
        self._commit_reasoning_history()
        self._invalidate()

    def _commit_reasoning_history(self) -> None:
        """Copy finished real reasoning into the scrollback exactly once."""
        reasoning = (self.state.thinking_text or "").strip()
        if not reasoning or reasoning == self.state.REASONING_FALLBACK:
            return
        if self._reasoning_committed:
            return
        self._reasoning_committed = True
        from rich.text import Text

        from .theme import active_palette

        palette = active_palette()
        self.console.print()
        self.console.print(
            Text("  Thinking", style=f"bold {palette.muted}"), highlight=False
        )
        for line in reasoning.splitlines():
            if not line.strip():
                continue
            block = Text()
            block.append("  \u2503 ", style=palette.info)
            block.append(line.rstrip(), style=palette.dim)
            self.console.print(block, highlight=False, overflow="fold")

    def reset_reasoning_history(self) -> None:
        """A new turn may record its own reasoning block."""
        self._reasoning_committed = False

    # --- state --------------------------------------------------------------
    def sync_from_config(self, config: Any) -> None:
        self.state.sync_from_config(config)
        # The reasoning level is real configuration when it exists; when the
        # config has no such setting the composer shows nothing for it.
        reasoning = ""
        for attribute in ("reasoning", "reasoning_level", "effort"):
            value = getattr(config, attribute, "")
            if isinstance(value, str) and value.strip():
                reasoning = " ".join(value.split())
                break
        if reasoning != self.state.reasoning:
            self.state.update(reasoning=reasoning)
        self._invalidate()

    # --- background initialization (a non-blocking UI state) ----------------
    def begin_initialization(self, label: str = "Agent Mode") -> None:
        """Show ``<mode> / Initializing…`` while a switch prepares in the
        background. Called from the worker thread; it only touches state and
        asks for a repaint, so the interface stays live.

        Idempotent: the controller shows it the instant the mode is requested,
        and the worker announces it again when the command really starts, so
        only the first call may append a line.
        """
        if self.state.agent_initializing:
            return
        self.state.update(agent_initializing=True)
        self.state.set_status(Status.INITIALIZING, "initializing")
        self.append_activity("note", f"{label} / Initializing...")
        self._invalidate()

    def end_initialization(self) -> None:
        """Leave the initializing state (a no-op when it is not showing)."""
        if not self.state.agent_initializing:
            return
        self.state.update(agent_initializing=False)
        if self.state.status is Status.INITIALIZING:
            self.state.set_status(Status.READY, "")
        self._invalidate()

    # --- scrolling ----------------------------------------------------------
    def scroll_by(self, delta: int) -> None:
        self._follow = False
        self._scroll = max(0, self._scroll + delta)
        self._invalidate()

    def scroll_to_bottom(self) -> None:
        self._follow = True
        self._invalidate()

    def _inline_plan_lines(self, width: int) -> list[StyledLine]:
        """The task plan rendered inline at the end of the conversation.

        v10.2.0 removes the permanent right-side panel: the plan is part of the
        conversation timeline, at full width, and only when a real plan exists.
        """
        if not self.state.plan_nodes:
            return []
        from .theme import active_palette

        palette = active_palette()
        lines: list[StyledLine] = [
            [],
            [(f"bold fg:{palette.primary}", " Task Progress")],
        ]
        lines.extend(self._plan_fragments(max(10, width - 1), 0))
        return lines

    def _slice(self, width: int, height: int) -> list[StyledLine]:
        # The empty state: a centred wordmark, placeholder and one tip, shown
        # only until the first message (or any real output) arrives.
        if (
            self._intro_visible
            and not self._busy
            and not self.state.messages
            and not self.buffer.lines()
        ):
            from .intro import intro_lines

            return intro_lines(self.state, width, height)
        # The live plan is part of the conversation, not a reserved column:
        # appended after the real transcript so the full width stays free.
        lines = self.buffer.lines() + self._inline_plan_lines(width)
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

    def _composer_accent(self) -> list[tuple[str, str]]:
        palette = active_palette()
        count = max(
            1,
            self._composer_top_rows
            + self._input_rows
            + self._composer_meta_rows
            + self._composer_rule_rows,
        )
        colours = gradient_colors(palette.brand_start, palette.brand_end, count)
        fragments: list[tuple[str, str]] = []
        for index, colour in enumerate(colours):
            if index:
                fragments.append(("", "\n"))
            glyph = "\u2579" if self._composer_rule_rows and index == count - 1 else "\u2503"
            fragments.append((f"fg:{colour}", glyph))
        return fragments

    def _composer_metadata(self) -> list[tuple[str, str]]:
        from .theme import active_palette

        palette = active_palette()
        # One restrained hierarchy: the agent mode is the only accented value,
        # the model is normal text, the provider is secondary and the reasoning
        # level is the quietest metadata. Values that do not exist are omitted
        # entirely (never replaced by a placeholder).
        segments: list[tuple[str, str]] = [
            (self.state.agent_state or "Build", f"bold fg:{palette.info}"),
            (self.state.model, f"fg:{palette.text}"),
            (self.state.provider, f"fg:{palette.muted}"),
            (self.state.reasoning, f"bold fg:{palette.highlight}"),
        ]
        segments = [(text, style) for text, style in segments if text]
        if not segments:
            return []
        limit = max(0, self._width - 6)
        separator = "  \u00b7  "
        fragments: list[tuple[str, str]] = []
        used = 0
        for index, (text, style) in enumerate(segments):
            prefix = separator if index else ""
            room = limit - used - len(prefix)
            if room <= 0:
                break
            if prefix:
                fragments.append((f"fg:{palette.dim}", prefix))
            if len(text) > room:
                clipped = text[: max(0, room - 1)] + "\u2026" if room > 1 else text[:room]
                fragments.append((style, clipped))
                break
            fragments.append((style, text))
            used += len(prefix) + len(text)
        return fragments

    def _workspace_fragments(self) -> list[tuple[str, str]]:
        # The permanent workspace row is gone (the reference never spends a
        # row on it); the path is available in /status and the header.
        return []

    def _workspace_fragments_legacy(self) -> list[tuple[str, str]]:
        from .theme import active_palette

        path = safe_text(self.state.workspace or "").strip()
        if not path:
            return []
        label = "Workspace: "
        width = max(0, self._width - len(label))
        if len(path) > width:
            if width <= 1:
                path = "..."[:width] if width else ""
            else:
                left = max(0, (width - 3) // 2)
                right = max(0, width - left - 3)
                path = path[:left] + "..." + (path[-right:] if right else "")
        return [(f"fg:{active_palette().muted}", label + path)]

    def _composer_rule(self) -> list[tuple[str, str]]:
        return [("class:composer", " ")]

    # --- slash-command autocomplete -----------------------------------------
    def _command_entries(self) -> list[tuple[str, str]]:
        """The real registered commands (name, description), loaded once.

        Drawn from the live dispatch registry (``seedcode.commands``), so a
        suggestion can never drift from a command the backend dispatches. A
        registry that fails to import simply suggests nothing.
        """
        if self._command_catalog is None:
            try:
                from ..commands import _REGISTRY

                self._command_catalog = sorted(
                    (name, str(entry[1])) for name, entry in _REGISTRY.items()
                )
            except Exception:
                self._command_catalog = []
        return self._command_catalog

    def _on_input_changed(self, buffer: Buffer) -> None:
        """Filter the suggestions to the command being typed.

        The list appears while the buffer holds exactly one ``/token``
        (a single word, no newline) and narrows with every character. As soon
        as the user moves to the arguments the list disappears.
        """
        text = buffer.text
        entries: list[tuple[str, str]] = []
        if text.startswith("/") and "\n" not in text and " " not in text:
            query = text[1:].lower()
            entries = [
                (name, help_text)
                for name, help_text in self._command_entries()
                if name.lower().startswith(query)
            ]
            if not entries:
                # Prefix miss: try a substring match before giving up, so
                # /theme still finds /workspace-like typos being typed.
                entries = [
                    (name, help_text)
                    for name, help_text in self._command_entries()
                    if query and query in name.lower()
                ]
        if entries != self._auto_entries:
            self._auto_entries = entries
            self._auto_index = 0
        elif self._auto_entries:
            self._auto_index %= len(self._auto_entries)
        # The row budget is re-fit immediately (not only on the next frame's
        # before_render) so the layout is already correct when it paints.
        self._reallocate_rows()
        self._invalidate()

    def _auto_display_entries(self) -> list[tuple[str, str]]:
        """The visible suggestions (the list is bounded)."""
        return list(self._auto_entries[:_MAX_AUTOCOMPLETE_ROWS])

    def _inline_selection_is_complete(self) -> bool:
        """True when the buffer already holds exactly one highlighted command.

        Typing ``/help`` matches only ``/help``; the completion would replace
        the buffer with text the user cannot distinguish from what was typed,
        so Enter must submit instead of looping back into the same selection.
        A proper prefix (``/hel``, ``/wor`` for ``/workspace set``) still
        completes first.
        """
        entries = self._auto_display_entries()
        if len(entries) != 1:
            return False
        typed = self._input.text.strip().lower()
        return typed == f"/{entries[0][0].lower()}"

    def _complete_selected(self) -> None:
        """Replace the typed token with the highlighted suggestion.

        Completion ends with a trailing space (the argument position the
        completed command expects) and leaves the composer focused, so Enter
        submits exactly what is on screen — no hidden state.
        """
        entries = self._auto_display_entries()
        if not entries:
            return
        name = entries[min(self._auto_index, len(entries) - 1)][0]
        self._input.text = f"/{name} "
        self._input.cursor_position = len(self._input.text)
        self._invalidate()

    def _autocomplete_move(self, step: int) -> None:
        """Highlight the previous/next suggestion (wrap both ways)."""
        if self._auto_entries:
            self._auto_index = (self._auto_index + step) % len(self._auto_entries)
            self._invalidate()

    def _autocomplete_pick(self, row: int) -> None:
        """Mouse: clicking a suggestion row selects and completes it."""
        entries = self._auto_display_entries()
        if 0 <= row < len(entries):
            self._auto_index = row
            self._complete_selected()

    def _dismiss_autocomplete(self) -> None:
        """Close the suggestion list, keeping what is typed."""
        if self._auto_entries:
            self._auto_entries = []
            self._auto_rows = 0
            self._invalidate()

    def _auto_row_count(self) -> int:
        return len(self._auto_display_entries())

    def _autocomplete_fragments(self) -> list[list[tuple[str, str]]]:
        """The rendered suggestion rows (one line per displayed command)."""
        from .theme import active_palette

        palette = active_palette()
        entries = self._auto_display_entries()
        if not entries:
            return [[("", "")]]
        rows: list[list[tuple[str, str]]] = []
        selected = min(self._auto_index, len(entries) - 1)
        for index, (name, help_text) in enumerate(entries):
            is_selected = index == selected
            marker = "\u203a " if is_selected else "  "
            description = str(help_text or "")
            budget = max(4, self._width - len(name) - 6)
            description = description[:budget]
            rows.append(
                [
                    (
                        f"fg:{palette.accent}" if is_selected else f"fg:{palette.dim}",
                        marker,
                    ),
                    (
                        f"bold fg:{palette.primary}"
                        if is_selected
                        else f"fg:{palette.text}",
                        f"/{name}",
                    ),
                    (f"fg:{palette.dim}", f"  {description}"),
                ]
            )
        return rows

    # --- responsive row allocation ------------------------------------------
    def _on_ui_thread(self) -> bool:
        """Whether the caller is the thread running the prompt_toolkit loop."""
        return self._ui_thread is not None and threading.get_ident() == self._ui_thread

    def _header_row_count(self) -> int:
        # The home screen owns its own logo and chat owns its message stream;
        # identity lives in the composer, so no permanent top bar is needed.
        return 0

    def _reallocate_rows(self) -> None:
        """Fit every region into the live terminal height, in priority order.

        This is what makes the interface genuinely responsive: instead of a
        fixed stack whose minimum heights add up to more than a short terminal
        (which makes prompt_toolkit's ``HSplit`` give up and render nothing at
        all — the blank screen), each region is given rows out of a budget for
        the current size. The composer is served first (it must always be
        usable), then the header, then the permission panel; the conversation
        window is the flexible one and absorbs whatever is left.

        The result is only ever *numbers*: the windows themselves are built
        once and never re-created, so a resize is a re-fit and a repaint rather
        than a rebuild.
        """
        rows = max(1, self._rows)
        budget = rows

        budget -= 1 if budget > _MIN_CONVERSATION_ROWS else 0  # conversation
        # The bottom status bar always owns one row; the home screen adds the
        # shortcut hint above it. Chat keeps the same bar with cwd/tokens.
        status = 1 if budget > 1 else 0
        budget -= status
        hint = 0
        if self._intro_visible and budget - _HINT_ROWS >= 1:
            hint = _HINT_ROWS
            budget -= hint
        # One row of breathing room inside the box, then the status line and
        # its soft bottom edge.
        self._composer_top_rows = 1 if rows >= 10 and budget >= 4 else 0
        self._composer_meta_rows = 1 if rows >= 8 and budget >= 3 else 0
        self._composer_rule_rows = 1 if rows >= 7 and budget >= 2 else 0
        composer_extra = (
            self._composer_top_rows
            + self._composer_meta_rows
            + self._composer_rule_rows
        )
        minimum_input = min(_MIN_INPUT_LINES, max(0, budget - composer_extra))
        # The full workspace path never occupies a permanent bottom row; it is
        # available through /status and the header.
        workspace = 0
        input_rows = max(
            0, min(self._input_height(), budget - composer_extra - workspace)
        )
        budget -= input_rows + composer_extra + workspace
        # The Thinking strip is reserved directly above the composer (it is
        # compact and only exists while there is reasoning to show), then the
        # header, then the transient lists.
        thinking = min(self._thinking_height(), max(0, budget))
        budget -= thinking
        header = min(self._header_row_count(), max(0, budget))
        budget -= header
        # The suggestion list is transient but bounded: served after the
        # header, it never squeezes the composer — the conversation gives.
        self._auto_rows = min(self._auto_row_count(), max(0, budget))
        budget -= self._auto_rows
        permission = min(self._permission_height(), max(0, budget))

        self._hint_rows = hint
        self._workspace_rows = workspace
        self._status_rows = status
        self._input_rows = input_rows
        self._thinking_rows = thinking
        self._header_rows = header
        self._permission_rows = permission

    def min_layout_rows(self) -> int:
        """Rows the fixed regions need at the current size.

        prompt_toolkit renders *nothing* when the minimum heights of a vertical
        layout exceed the terminal height, so this is the invariant that keeps
        a short terminal usable (a compressed interface) instead of blank.
        """
        return (
            self._header_rows
            + self._auto_rows
            + self._permission_rows
            +            self._thinking_rows
            + self._input_rows
            + self._composer_top_rows
            + self._composer_meta_rows
            + self._composer_rule_rows
            + self._workspace_rows
            + self._hint_rows
            + self._status_rows
        )

    def _header_dimension(self) -> Dimension:
        return Dimension.exact(self._header_rows)

    def _auto_dimension(self) -> Dimension:
        return Dimension.exact(self._auto_rows)

    def _input_dimension(self) -> Dimension:
        return Dimension.exact(self._input_rows)

    def _composer_top_dimension(self) -> Dimension:
        return Dimension.exact(self._composer_top_rows)

    def _composer_meta_dimension(self) -> Dimension:
        return Dimension.exact(self._composer_meta_rows)

    def _composer_rule_dimension(self) -> Dimension:
        return Dimension.exact(self._composer_rule_rows)

    def _workspace_dimension(self) -> Dimension:
        return Dimension.exact(self._workspace_rows)

    def _permission_dimension(self) -> Dimension:
        return Dimension.exact(self._permission_rows)

    def _hint_dimension(self) -> Dimension:
        return Dimension.exact(self._hint_rows)

    def _status_dimension(self) -> Dimension:
        return Dimension.exact(self._status_rows)

    # --- v9.2.0: TASK PLAN + THINKING panels ---------------------------------
    def _visible_plan_nodes(self) -> list[tuple[str, str, int, int, bool]]:
        """The tree rows the panel shows, with collapsed subtrees hidden.

        Collapsing is a display state of the real tree (the ids are the task
        graph's own), so hiding a subtree never changes task state or progress.
        """
        rows = list(self.state.plan_nodes or [])
        closed = {int(i) for i in (self.state.plan_collapsed or [])}
        visible: list[tuple[str, str, int, int, bool]] = []
        hidden_at: int | None = None
        for state, title, depth, task_id, has_children in rows:
            depth = int(depth)
            if hidden_at is not None:
                if depth > hidden_at:
                    continue
                hidden_at = None
            visible.append((state, title, depth, task_id, has_children))
            if has_children and int(task_id) in closed:
                hidden_at = depth
        return visible

    def _plan_fragments(self, width: int, height: int) -> list[StyledLine]:
        """The right-side TODO rows, styled straight from the real task graph."""
        from .taskpanel import state_role, task_glyph, tree_progress
        from .theme import active_palette

        palette = active_palette()
        rows = self._visible_plan_nodes()
        closed = {int(i) for i in (self.state.plan_collapsed or [])}
        lines: list[StyledLine] = []
        inner = max(1, (width or 1) - 1)
        for state, title, depth, task_id, has_children in rows:
            style = f"fg:{getattr(palette, state_role(state), palette.dim)}"
            arrow = ""
            if has_children:
                arrow = "+ " if int(task_id) in closed else "- "
            prefix = f"{'  ' * max(0, int(depth))}{arrow}{task_glyph(state)} "
            title = " ".join(str(title or "").split())
            room = max(1, inner - len(prefix))
            clipped = title if len(title) <= room else title[: max(0, room - 1)] + "\u2026"
            lines.append([(style, f" {prefix}"), (f"fg:{palette.text}", clipped)])
        progress = tree_progress(
            list(self.state.plan_nodes or []), self.state.plan_percent
        )
        if progress:
            lines.append([(f"fg:{palette.dim}", f" {progress}")])
        return lines

    def _plan_height(self) -> int:
        """Panel rows: one per visible task plus the progress line (0 = hidden)."""
        if not self.state.plan_nodes:
            return 0
        return len(self._visible_plan_nodes()) + 1

    # --- compact live activity ----------------------------------------------
    def _thinking_fragments(self, width: int, height: int) -> list[StyledLine]:
        """Render current operation and recent real events, never model thoughts."""
        from .theme import active_palette

        palette = active_palette()
        visible = max(1, int(height or 1))
        lines: list[StyledLine] = []
        # One operation line, updated in place. The spinner and shimmer come
        # from the UI clock; the text is always the real backend event.
        if self._live_action is not None:
            kind, text = self._live_action
            body = text[: max(4, int(width) - 6)]
            if kind == "success":
                lines.append(
                    [(f"fg:{palette.success}", "  \u2713 "), (f"fg:{palette.text}", body)]
                )
            elif kind == "error":
                lines.append(
                    [(f"fg:{palette.error}", "  \u2715 "), (f"fg:{palette.error}", body)]
                )
            else:
                frame = _SPINNER_FRAMES[self._think_index % len(_SPINNER_FRAMES)]
                glyph: StyledLine = [(f"fg:{palette.highlight}", f"  {frame} ")]
                glyph.extend(
                    shimmer_fragments(body, palette.muted, palette.highlight, self._think_index * 0.45)
                )
                lines.append(glyph)
        reasoning = (self.state.thinking_text or "").replace("\r\n", "\n")
        # The "reasoning unavailable" record is history, not a live Thinking
        # state: a model that exposes no reasoning must show NO panel at all.
        if reasoning.strip() == self.state.REASONING_FALLBACK:
            reasoning = ""
        if not reasoning.strip():
            return lines
        # Wrap to the terminal width so a streamed chunk is visible immediately
        # even before the model emits its first newline.
        import textwrap

        room = max(8, int(width) - 4)
        wrapped: list[str] = []
        for raw in reasoning.split("\n"):
            if not raw.strip():
                wrapped.append("")
                continue
            wrapped.extend(
                textwrap.wrap(raw, width=room, replace_whitespace=False) or [raw]
            )
        offset = max(0, int(self.state.thinking_offset or 0))
        end = max(0, len(wrapped) - offset)
        start = max(0, end - max(0, visible - len(lines) - 1))
        shown = wrapped[start:end]
        # ``Thinking · 3.2s`` exists only while a turn is active AND real
        # reasoning has arrived; the elapsed clock is the real request clock.
        heading: StyledLine = [(f"bold fg:{palette.dim}", " Thinking")]
        if self._thinking_active and self._think_started:
            elapsed = max(0.0, time.monotonic() - self._think_started)
            heading.append((f"fg:{palette.dim}", f" \u00b7 {elapsed:.1f}s"))
        rail = f"fg:{palette.info}"
        lines.append(heading)
        for text in shown:
            lines.append([(rail, " \u2503 "), (f"fg:{palette.dim}", safe_text(text))])
        return lines

    def _thinking_height(self) -> int:
        """Compact height for current or recent activity; 0 when empty/collapsed."""
        if self._live_action is not None:
            if self._rows < 8:
                return 0
            return 1
        if not self.state.thinking_open:
            return 0
        reasoning = (self.state.thinking_text or "").strip()
        if not reasoning or reasoning == self.state.REASONING_FALLBACK:
            return 0  # no real reasoning → no Thinking UI at all
        if self._rows < 10:
            return 0
        # Bounded by the terminal: never more than a third of the screen, so
        # the conversation and the composer always remain usable.
        return min(8, max(2, self._rows // 3))

    def _plan_dimension(self) -> Dimension:
        rows = self._plan_height()
        return Dimension.exact(rows if rows > 0 else 0)

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
            # Selected state transitions run inside the live application (no
            # exit, no rebuild, no flicker). Everything else may open a
            # selector/menu that needs the terminal to itself, so it hands
            # control back to the controller for one dispatch.
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
    def confirm(
        self,
        title: str,
        question: str,
        description: str,
        *,
        cancel: Callable[[], bool] | None = None,
    ) -> str:
        """Ask from the worker thread; answer on the main thread. Deny on timeout.

        The request is rendered as a bounded, keyboard-driven panel inside the
        existing application (never a nested prompt, never a blocking
        ``input()``): only the agent turn waits, and ``Enter`` allows, ``A``
        allows for the session, ``D`` denies and ``Esc`` cancels.

        **The one rule this method enforces on itself:** it may never be called
        from the thread that runs the event loop. The answer to the panel is
        delivered by that event loop, so a caller inside it would wait forever
        for a key press it has already made impossible — the deadlock that froze
        the terminal during an Agent Mode switch. Such a call is refused (denied,
        with an explanation on screen) instead of hanging.
        """
        if self._on_ui_thread():
            self.append_activity(
                "error",
                "Permission request could not be shown: it arrived on the "
                "interface thread, which is the only one that can answer it. "
                "The action was denied. Nothing is frozen — switch modes or "
                "carry on.",
            )
            self.state.set_status(Status.ERROR, "permission request refused")
            self._invalidate()
            return "n"
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
                if cancel is not None and cancel():
                    # Superseded: a newer mode switch owns the screen, so this
                    # ask is abandoned instead of holding a worker (and the
                    # composer's Enter key) hostage.
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

        ``y`` / Enter -> allow once, ``a`` -> allow this action type for this
        session, ``A`` (Shift+A) -> allow *every* action type for this session,
        ``d`` / ``n`` / Esc -> deny. Unknown input denies, so a stray key can
        never silently grant a dangerous action.
        """
        raw = (text or "").strip()
        key = raw.lower()[:1]
        if raw[:1] == "A":
            answer = "A"  # Allow All (all categories, this session)
        else:
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
        self._turn_started = time.monotonic()
        self._live_action = None
        self.append_user(text)
        self.state.add_message("user", text)
        # Thinking starts the moment the message is sent — before the provider
        # has been called — and is replaced by the answer as soon as it streams.
        self.state.set_status(Status.THINKING, "starting agent task")
        # Backend state only: the turn's real phase (no longer rendered —
        # the bottom strip is the compact reasoning display).
        self.reset_reasoning_history()
        # Each turn owns its reasoning window: the previous turn's text was
        # committed to the scrollback, so the live strip starts empty and the
        # Thinking label cannot appear before this turn's first real delta.
        self.state.reset_thinking()
        self.state.set_briefing(
            active=True,
            status="Thinking",
            task=safe_text(text).strip()[:160],
            activity="Starting agent task",
            last_completed="",
            next_action="",
            retry="",
            error="",
        )
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
            # The turn is over: the briefing must not claim the model is working
            # and no stale spinner line may survive into the next turn.
            self.state.reset_briefing()
            self._live_action = None
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
        """Re-fit the regions to the live terminal size before each frame.

        Deliberately minimal: it records the new width/height, re-runs the row
        budget and asks for one repaint when the width actually changed (which
        re-wraps the streamed answer in place). It never touches a window, a
        container or the layout tree, because a resize must not rebuild the
        interface — only re-fit and redraw it.
        """
        try:
            size = app.output.get_size()
        except Exception:
            return
        width = max(20, int(size.columns or 80))
        rows = max(_MIN_TERMINAL_ROWS, int(size.rows or 24))
        resized = width != self._width or rows != self._rows
        self._width = width
        self._rows = rows
        if getattr(self.console, "_width", None) != width:
            self.console._width = width  # type: ignore[attr-defined]
        self._reallocate_rows()
        if resized and self._stream is not None:
            # A streamed answer re-flows to the new width instead of keeping
            # the wrapping it was rendered with at the old one.
            self._stream.reflow()

    # --- fragments ----------------------------------------------------------
    def _header_fragments(self) -> list[tuple[str, str]]:
        lines = header_lines(self.state, self._width)
        flat: list[tuple[str, str]] = []
        for index, line in enumerate(lines):
            if index:
                flat.append(("", "\n"))
            flat.extend(line)
        return flat

    def _composer_bottom(self) -> list[tuple[str, str]]:
        """The right-aligned shortcut row directly under the home composer."""
        palette = active_palette()
        parts: list[tuple[str, str]] = []
        if self._busy or self.state.agent_initializing:
            parts.append((f"fg:{palette.warning}", "Esc interrupt"))
            parts.append((f"fg:{palette.muted}", "   "))
        parts.extend(
            [
                (f"bold fg:{palette.text}", "tab"),
                (f"fg:{palette.muted}", " agents   "),
                (f"bold fg:{palette.text}", "ctrl+p"),
                (f"fg:{palette.muted}", " commands"),
            ]
        )
        if not self._follow and self._unseen > 0:
            parts.append((f"fg:{palette.warning}", f"   \u2193 {self._unseen} new"))
        width = sum(len(text) for _style, text in parts)
        box_left = self._composer_pad_width()
        box_width = max(20, self._width - box_left * 2)
        pad = box_left + max(0, box_width - width - 2)
        return [("", " " * pad)] + parts

    def _cwd_label(self) -> str:
        path = safe_text(self.state.workspace or "").strip()
        if not path:
            return ""
        try:
            from pathlib import Path

            home = str(Path.home())
            if path.lower().startswith(home.lower()):
                path = "~" + path[len(home) :]
        except Exception:
            pass
        return path

    def _status_fragments(self) -> list[tuple[str, str]]:
        """The bottom-most status bar: cwd, context, shortcuts and version."""
        from .. import __version__

        palette = active_palette()
        cwd = self._cwd_label()
        if len(cwd) > max(8, self._width - 28):
            keep = max(4, self._width - 31)
            cwd = "\u2026" + cwd[-keep:]
        left: list[tuple[str, str]] = []
        if cwd:
            left.append((f"fg:{palette.text}", cwd))
        if self.state.context_limit:
            def _short(value: int) -> str:
                if value >= 1000:
                    return f"{value / 1000:.1f}k".replace(".0k", "k")
                return str(value)

            if left:
                left.append(
                    (
                        f"fg:{palette.muted}",
                        "   "
                        + _short(int(self.state.context_used or 0))
                        + "/"
                        + _short(int(self.state.context_limit))
                        + " ctx",
                    )
                )
        right: list[tuple[str, str]] = []
        if self._intro_visible:
            if self.state.connection:
                right.append((f"fg:{palette.success}", "\u25cf "))
                right.append((f"fg:{palette.muted}", self.state.connection + "   "))
            right.append((f"fg:{palette.muted}", "/status   "))
            right.append((f"fg:{palette.muted}", "v" + __version__))
        else:
            right.extend(
                [
                    (f"bold fg:{palette.text}", "ctrl+p"),
                    (f"fg:{palette.muted}", " commands"),
                ]
            )
        used = sum(len(text) for _style, text in left + right)
        gap = max(1, self._width - used - 2)
        return [(f"fg:{palette.muted}", " ")] + left + [(f"fg:{palette.muted}", " " * gap)] + right + [(f"fg:{palette.muted}", " ")]

    def _composer_pad_width(self) -> int:
        if not self._intro_visible:
            return 0
        box = min(80, max(36, int(self._width * 0.68)))
        return max(0, (self._width - box) // 2)

    def _composer_pad_dimension(self) -> Dimension:
        return Dimension.exact(self._composer_pad_width())

    def _build_app(self) -> Application:
        kb = KeyBindings()

        @kb.add("enter")
        def _(_event) -> None:
            # With the suggestion list open Enter completes the highlighted
            # command instead of submitting — unless the buffer already is
            # exactly the highlighted command (`/help` typed in full), in
            # which case completing again would be a no-op and the composer
            # would wait forever for a second Enter that never adds anything.
            if self._auto_entries and not self._inline_selection_is_complete():
                self._complete_selected()
                return
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
            if self._auto_entries:
                self._complete_selected()
                return
            event.app.layout.focus_next()

        @kb.add("s-tab")
        def _(event) -> None:
            event.app.layout.focus_previous()

        @kb.add("up")
        def _(_event) -> None:
            # With the suggestion list open Up/Down move the selection; only
            # when it is closed does the cursor/history movement apply.
            if self._auto_entries:
                self._autocomplete_move(-1)
                return
            # Inside a multiline message Up/Down move the cursor; only at the
            # top/bottom edge do they walk the input history.
            if self._input.document.cursor_position_row > 0:
                self._input.cursor_up()
            else:
                self._history_move(-1)

        @kb.add("down")
        def _(_event) -> None:
            if self._auto_entries:
                self._autocomplete_move(1)
                return
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

        @kb.add("c-e")
        def _(_event) -> None:
            self.show_last_change()

        @kb.add("c-t")
        def _(_event) -> None:
            self.show_last_command()

        # v9.2.0: the thinking panel — Ctrl+B collapses/expands it, Ctrl+Up/
        # Ctrl+Down scroll it manually. While scrolled up the panel keeps the
        # user's position (no forced auto-scroll back down); auto-scroll
        # resumes when they return to the bottom.
        @kb.add("c-b")
        def _(_event) -> None:
            self.state.toggle_thinking()
            self._invalidate()

        # v9.3.1: the TODO tree — Ctrl+O collapses / expands the container
        # tasks (every container when nothing is selected).
        @kb.add("c-o")
        def _(_event) -> None:
            self.state.toggle_plan_collapse()
            self._invalidate()

        @kb.add("c-up")
        def _(_event) -> None:
            if (self.state.thinking_text or "").strip():
                self.state.scroll_thinking(3)
                self._invalidate()

        @kb.add("c-down")
        def _(_event) -> None:
            if self.state.thinking_offset > 0:
                self.state.scroll_thinking(-3)
                self._invalidate()

        @kb.add("c-c")
        def _(_event) -> None:
            if self._deny_confirm():
                return
            if self._busy:
                self.request_cancel()
            elif self.state.agent_initializing and self.on_cancel_init is not None:
                # The event loop is never blocked by initialization, so this
                # reaches the worker immediately.
                self.on_cancel_init()
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
            if self._auto_entries:
                # Esc dismisses the suggestions but keeps what is typed.
                self._dismiss_autocomplete()
                return
            if self._busy:
                self.request_cancel()
            elif self.state.agent_initializing and not self._input.text:
                if self.on_cancel_init is not None:
                    self.on_cancel_init()
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
        # Every fixed region takes its height from a *dynamic* dimension: the
        # callable is re-read by prompt_toolkit on each layout pass, so the
        # window objects stay exactly as built while a resize re-fits them.
        self._header_window = Window(
            FormattedTextControl(self._header_fragments, focusable=False),
            height=self._header_dimension,
            dont_extend_height=True,
        )
        self._input_window = Window(
            BufferControl(buffer=self._input, input_processors=[_PlaceholderProcessor()]),
            height=self._input_dimension,
            dont_extend_height=True,
            wrap_lines=True,
            style="class:composer",
        )
        # The permission panel is a persistent region (height 0 unless a
        # request is pending), never a nested application or a blocking read.
        self._permission_window = Window(
            FormattedTextControl(self._permission_fragments, focusable=False),
            height=self._permission_dimension,
            dont_extend_height=True,
            wrap_lines=False,
        )
        self._hint_window = Window(
            FormattedTextControl(self._composer_bottom, focusable=False),
            height=self._hint_dimension,
            dont_extend_height=True,
        )
        self._status_window = Window(
            FormattedTextControl(self._status_fragments, focusable=False),
            height=self._status_dimension,
            dont_extend_height=True,
        )
        self._workspace_window = Window(
            FormattedTextControl(self._workspace_fragments, focusable=False),
            height=self._workspace_dimension,
            dont_extend_height=True,
        )
        composer_content = HSplit(
            [
                Window(
                    height=self._composer_top_dimension,
                    style="class:composer",
                    dont_extend_height=True,
                ),
                self._input_window,
                Window(
                    FormattedTextControl(self._composer_metadata, focusable=False),
                    height=self._composer_meta_dimension,
                    style="class:composer",
                    dont_extend_height=True,
                ),
                Window(
                    FormattedTextControl(self._composer_rule, focusable=False),
                    height=self._composer_rule_dimension,
                    style="class:composer",
                    dont_extend_height=True,
                ),
            ],
            style="class:composer",
        )
        composer = VSplit(
            [
                Window(width=self._composer_pad_dimension, style="class:canvas"),
                Window(
                    FormattedTextControl(self._composer_accent, focusable=False),
                    width=Dimension.exact(1),
                    style="class:composer",
                    dont_extend_width=True,
                ),
                Window(width=Dimension.exact(2), style="class:composer"),
                composer_content,
                Window(width=Dimension.exact(2), style="class:composer"),
                Window(width=self._composer_pad_dimension, style="class:canvas"),
            ],
            style="class:canvas",
        )
        self._autocomplete_window = Window(
            _AutocompleteControl(
                lambda w, h: self._autocomplete_fragments(),
                self._autocomplete_pick,
            ),
            height=self._auto_dimension,
            dont_extend_height=True,
            wrap_lines=False,
        )
        # v10.2.0: no permanent right-side plan/sidebar window. The plan is
        # rendered inline in the content region (see _inline_plan_lines).
        self._plan_window = None
        self._thinking_window = Window(
            _ThinkingControl(self._thinking_fragments, self._thinking_height),
            dont_extend_height=True,
            wrap_lines=False,
        )
        root = HSplit(
            [
                self._header_window,
                # Full-width conversation: activity, diff, inline task plan.
                self._content_window,
                self._autocomplete_window,
                self._permission_window,
                # The event-backed activity area stays above the composer.
                self._thinking_window,
                composer,
                self._workspace_window,
                self._hint_window,
                self._status_window,
            ],
            # The layout root paints the whole rect; gaps between rows can
            # never reveal the terminal's own profile background.
            style="class:canvas",
        )
        return Application(
            layout=Layout(root, focused_element=self._input_window),
            key_bindings=kb,
            style=DynamicStyle(lambda: pt_style()),
            cursor=CursorShape.BLOCK,
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
        # Remember which thread owns the event loop: a permission ask arriving
        # here could never be answered (see :meth:`confirm`).
        self._ui_thread = threading.get_ident()
        try:
            result = self._app.run()
        except (KeyboardInterrupt, EOFError):
            result = ("exit", None)
        except Exception:
            result = ("exit", None)
        finally:
            self._ui_thread = None
        if not isinstance(result, tuple) or len(result) != 2:
            return ("exit", None)
        kind, payload = result
        if kind == "exit":
            self._app = None  # the session is over; release the application
        return (str(kind), payload if isinstance(payload, str) else None)

    def shutdown(self) -> None:
        """Stop background UI work and release the application (called once)."""
        self.stop_thinking()
        # No background UI state survives the session: a workspace
        # preparation left mid-flight must not outlive the exit path.
        self.end_initialization()
        self._cancel.set()
        self._app = None

    def banner_text(self) -> None:
        """Show the centred intro in the conversation area.

        Nothing is printed into the scrollback: the intro is the empty state of
        the one conversation region, so it disappears naturally when the first
        message (or the first real tool event) is appended.
        """
        self._intro_visible = True
        self._invalidate()

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
    """Give assistant markdown a muted two-cell gradient rail.

    There is no ``AI >`` attribution: the response is identified by its rail
    and the model footer underneath it. The green→blue gradient runs down the
    whole answer and is blended toward the background so the words stay primary.
    """
    body = list(lines)
    while body and not "".join(text for _, text in body[0]).strip():
        body.pop(0)
    if not body:
        return [[("", "")]]
    palette = active_palette()
    colours = gradient_colors(palette.brand_start, palette.brand_end, len(body))
    out: list[StyledLine] = []
    for line, colour in zip(body, colours):
        muted = mix(colour, palette.bg, 0.40)
        out.append([(f"fg:{muted}", _ASSISTANT_BAR)] + list(line))
    return out


def _markdown_lines(text: str, width: int) -> list[StyledLine]:
    """Render assistant markdown to styled lines through a recording console."""
    from rich.markdown import Markdown

    from .content import parse_ansi, trim_line

    recorder = Console(
        file=_NullStream(),  # record only: never print to the real terminal
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
        width=max(20, width),
        theme=rich_theme(),
        style=f"on {active_palette().bg}",
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

    # --- structured live activity ------------------------------------------
    def append_activity(self, kind: str, message: str) -> None:
        """One real backend activity message in the persistent feed."""
        self.tui.append_activity(kind, message)

    def append_change(self, event: dict) -> None:
        """A real file change with its line ranges and diff preview."""
        self.tui.append_change(event)

    def record_command(self, command: str, ok: bool, output: str) -> None:
        """One real terminal command result, expandable with Ctrl+T."""
        self.tui.record_command(command, ok, output)

    def append_report(self, report: dict) -> None:
        """The turn's final execution report (from real observed events)."""
        self.tui.append_report(report)

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
        """Publish the real operation label during a blocking UI operation."""
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
        # Registered so a mid-stream resize re-wraps the answer in place.
        self.tui._stream = renderer
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
        finally:
            self.tui._stream = None

    # --- background mode initialization (a non-blocking UI state) ------------
    def begin_initialization(self, label: str = "Agent Mode") -> None:
        """Show ``<mode> / Initializing…`` (called from the switch worker)."""
        self.tui.begin_initialization(label)

    def end_initialization(self) -> None:
        """Leave the initializing state (a safe no-op when it is not set)."""
        self.tui.end_initialization()

    # --- confirmations ------------------------------------------------------
    def _confirm(
        self,
        title: str,
        category_label: str,
        description: str,
        *,
        cancel: Callable[[], bool] | None = None,
    ) -> str:
        return self.tui.confirm(title, category_label, description, cancel=cancel)

    def confirm_tool_action(
        self,
        category_label: str,
        description: str,
        *,
        cancel: Callable[[], bool] | None = None,
    ) -> str:
        return self._confirm("Agent Action", category_label, description, cancel=cancel)
