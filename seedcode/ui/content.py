"""Content region storage and ANSI-to-styled-line conversion (v8.2.5).

The middle region of the TUI is a scrolling buffer of *styled lines*. Two
producers write into it:

* the existing Rich codebase, which prints rendered panels, tables and messages
  to a console — under the TUI that console's stream is a sink feeding raw ANSI
  text in here, so every command, menu and message keeps working unchanged and
  simply lands in the scrollback instead of the terminal;
* the live stream block, which the TUI replaces as the assistant's tokens
  arrive (``set_live``/``clear_live``).

:class:`ContentBuffer` parses ANSI incrementally: state (colour, bold, …) is
carried across writes and lines, so a style opened on one line and reset later
is honoured, and the parsed result is cached as lines rather than re-parsed on
every frame.

Everything here is pure display code plus one lock: no terminal I/O, no
prompt_toolkit import, so it is trivially unit-testable.
"""

from __future__ import annotations

import threading
from typing import Callable, Iterable

__all__ = [
    "ContentBuffer",
    "StyledLine",
    "ansi_to_lines",
    "parse_ansi",
    "plain_text",
    "trim_line",
]

#: One line is a list of ``(style, text)`` fragments.
StyledLine = list

# --- ANSI ---------------------------------------------------------------------
_ESC = "\x1b"

_FG_NAMES = {
    30: "ansiblack",
    31: "ansired",
    32: "ansigreen",
    33: "ansiyellow",
    34: "ansiblue",
    35: "ansimagenta",
    36: "ansicyan",
    37: "ansigray",
}
_BRIGHT_NAMES = {
    90: "ansibrightblack",
    91: "ansibrightred",
    92: "ansibrightgreen",
    93: "ansibrightyellow",
    94: "ansibrightblue",
    95: "ansibrightmagenta",
    96: "ansibrightcyan",
    97: "ansibrightwhite",
}
_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)


def _xterm256(n: int) -> str:
    """``#rrggbb`` for an xterm-256 palette index."""
    n = max(0, min(255, int(n)))
    if n < 16:
        basic = (
            "#000000", "#cd0000", "#00cd00", "#cdcd00",
            "#0000ee", "#cd00cd", "#00cdcd", "#e5e5e5",
            "#7f7f7f", "#ff0000", "#00ff00", "#ffff00",
            "#5c5cff", "#ff00ff", "#00ffff", "#ffffff",
        )
        return basic[n]
    if n < 232:
        value = n - 16
        red = _CUBE_LEVELS[value // 36]
        green = _CUBE_LEVELS[(value // 6) % 6]
        blue = _CUBE_LEVELS[value % 6]
        return f"#{red:02x}{green:02x}{blue:02x}"
    grey = 8 + (n - 232) * 10
    return f"#{grey:02x}{grey:02x}{grey:02x}"


def _hex(r: int, g: int, b: int) -> str:
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


class _AnsiState:
    """Mutable SGR state carried across writes and lines."""

    __slots__ = ("fg", "bg", "bold", "dim", "italic", "underline")

    def __init__(self) -> None:
        self.fg: str | None = None
        self.bg: str | None = None
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False

    def reset(self) -> None:
        self.fg = None
        self.bg = None
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False

    def style(self) -> str:
        parts: list[str] = []
        if self.bold:
            parts.append("bold")
        if self.dim:
            parts.append("dim")
        if self.italic:
            parts.append("italic")
        if self.underline:
            parts.append("underline")
        if self.fg:
            parts.append(f"fg:{self.fg}")
        if self.bg:
            parts.append(f"bg:{self.bg}")
        return " ".join(parts)

    def apply(self, params: str) -> None:
        """Apply one SGR parameter list."""
        raw = [p for p in (params or "").split(";")]
        codes: list[int] = []
        for item in raw:
            item = item.strip()
            if not item:
                codes.append(0)
                continue
            try:
                codes.append(int(item))
            except ValueError:
                pass  # a private/unknown sub-parameter is ignored
        index = 0
        while index < len(codes):
            code = codes[index]
            if code == 0:
                self.reset()
            elif code == 1:
                self.bold = True
            elif code == 2:
                self.dim = True
            elif code == 3:
                self.italic = True
            elif code == 4:
                self.underline = True
            elif code == 22:
                self.bold = False
                self.dim = False
            elif code == 23:
                self.italic = False
            elif code == 24:
                self.underline = False
            elif code == 39:
                self.fg = None
            elif code == 49:
                self.bg = None
            elif code in _FG_NAMES:
                self.fg = _FG_NAMES[code]
            elif code in _BRIGHT_NAMES:
                self.fg = _BRIGHT_NAMES[code]
            elif 40 <= code <= 47:
                self.bg = _FG_NAMES.get(code - 10)
            elif 100 <= code <= 107:
                self.bg = _BRIGHT_NAMES.get(code - 10)
            elif code in (38, 48):
                target_fg = code == 38
                if index + 1 < len(codes) and codes[index + 1] == 5 and index + 2 < len(codes):
                    color = _xterm256(codes[index + 2])
                    index += 2
                    if target_fg:
                        self.fg = color
                    else:
                        self.bg = color
                elif (
                    index + 1 < len(codes)
                    and codes[index + 1] == 2
                    and index + 4 < len(codes)
                ):
                    color = _hex(codes[index + 2], codes[index + 3], codes[index + 4])
                    index += 4
                    if target_fg:
                        self.fg = color
                    else:
                        self.bg = color
            index += 1


class _AnsiParser:
    """Incremental ANSI parser producing styled lines."""

    def __init__(self) -> None:
        self.state = _AnsiState()
        self.line: list[tuple[str, str]] = []

    # --- fragments ---------------------------------------------------------
    def _append(self, text: str) -> None:
        if not text:
            return
        style = self.state.style()
        if self.line and self.line[-1][0] == style:
            self.line[-1] = (style, self.line[-1][1] + text)
        else:
            self.line.append((style, text))

    # --- escapes -----------------------------------------------------------
    def _handle_escape(self, text: str, index: int) -> int:
        """Consume one escape sequence starting at ``index``; return the next."""
        length = len(text)
        if index + 1 >= length:
            return length
        introducer = text[index + 1]
        if introducer == "[":
            cursor = index + 2
            while cursor < length and not ("@" <= text[cursor] <= "~"):
                cursor += 1
            if cursor >= length:
                return length  # incomplete sequence: drop it
            final = text[cursor]
            params = text[index + 2 : cursor]
            if final == "m":
                self.state.apply(params)
            return cursor + 1
        if introducer == "]":
            cursor = index + 2
            while cursor < length:
                if text[cursor] == "\x07":
                    return cursor + 1
                if text[cursor] == _ESC and cursor + 1 < length and text[cursor + 1] == "\\":
                    return cursor + 2
                cursor += 1
            return length
        return index + 2

    # --- feed --------------------------------------------------------------
    def feed(self, text: str) -> list[StyledLine]:
        """Feed ``text``; return the lines it completed (in order)."""
        completed: list[StyledLine] = []
        if not text:
            return completed
        index = 0
        run_start = 0
        length = len(text)
        while index < length:
            char = text[index]
            if char == _ESC:
                self._append(text[run_start:index])
                index = self._handle_escape(text, index)
                run_start = index
                continue
            if char == "\n":
                self._append(text[run_start:index])
                completed.append(self.line)
                self.line = []
                index += 1
                run_start = index
                continue
            if char == "\r":
                # Carriage return rewrites the current line.
                self._append(text[run_start:index])
                self.line = []
                index += 1
                run_start = index
                continue
            if char == "\t":
                self._append(text[run_start:index])
                self._append("    ")
                index += 1
                run_start = index
                continue
            index += 1
        self._append(text[run_start:index])
        return completed


def parse_ansi(text: str) -> list[StyledLine]:
    """Parse a complete ANSI string, including a trailing partial line."""
    parser = _AnsiParser()
    lines = parser.feed(text)
    if parser.line:
        lines.append(parser.line)
    return lines


def ansi_to_lines(text: str) -> list[StyledLine]:
    """Alias for :func:`parse_ansi` (readability at call sites)."""
    return parse_ansi(text)


def plain_text(lines: Iterable[StyledLine]) -> str:
    """The unstyled text of styled lines (for tests and logging)."""
    out: list[str] = []
    for line in lines:
        out.append("".join(fragment[1] for fragment in line))
    return "\n".join(out)


def trim_line(line: StyledLine) -> StyledLine:
    """Drop trailing whitespace from a styled line (keeps the styling)."""
    out = list(line)
    while out and not out[-1][1].strip():
        out.pop()
    if out:
        style, text = out[-1]
        stripped = text.rstrip()
        if stripped != text:
            out[-1] = (style, stripped)
    return out


# --- the buffer ---------------------------------------------------------------
class ContentBuffer:
    """Committed scrollback plus one replaceable live block.

    ``on_change`` fires whenever the visible content changed (a new line, a new
    live frame, a clear). The TUI uses it to request an incremental repaint.
    """

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._lock = threading.RLock()
        self._parser = _AnsiParser()
        self._lines: list[StyledLine] = []
        self._live: list[StyledLine] = []
        self._version = 0
        self.on_change = on_change

    # --- writes ------------------------------------------------------------
    def write(self, text: str) -> int:
        """Feed raw console output; returns how many lines completed."""
        if not text:
            return 0
        completed = self._parser.feed(text)
        changed = bool(completed)
        with self._lock:
            if completed:
                self._lines.extend(completed)
                self._version += 1
        if changed:
            self._changed()
        return len(completed)

    def commit(self, lines: Iterable[StyledLine]) -> None:
        """Append already-styled lines."""
        items = [list(line) for line in lines]
        if not items:
            return
        with self._lock:
            self._lines.extend(items)
            self._version += 1
        self._changed()

    def set_live(self, lines: Iterable[StyledLine]) -> None:
        """Replace the live block appended after the committed scrollback."""
        items = [list(line) for line in lines]
        with self._lock:
            if items == self._live:
                return
            self._live = items
            self._version += 1
        self._changed()

    def clear_live(self) -> None:
        with self._lock:
            if not self._live:
                return
            self._live = []
            self._version += 1
        self._changed()

    def commit_live(self) -> None:
        """Freeze the live block into the scrollback."""
        with self._lock:
            if not self._live:
                return
            self._lines.extend(self._live)
            self._live = []
            self._version += 1
        self._changed()

    def clear(self) -> None:
        with self._lock:
            self._parser = _AnsiParser()
            self._lines = []
            self._live = []
            self._version += 1
        self._changed()

    # --- reads -------------------------------------------------------------
    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def lines(self) -> list[StyledLine]:
        """Every visible line: committed, the partial line, then the live block."""
        with self._lock:
            lines = [list(line) for line in self._lines]
            if self._parser.line:
                lines.append(list(self._parser.line))
            if self._live:
                lines.extend(list(line) for line in self._live)
            return lines

    def line_count(self) -> int:
        with self._lock:
            return len(self._lines) + (1 if self._parser.line else 0) + len(self._live)

    def _changed(self) -> None:
        callback = self.on_change
        if callback is None:
            return
        try:
            callback()
        except Exception:
            pass  # a repaint request must never break the writer
