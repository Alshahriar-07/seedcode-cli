"""Live streaming markdown renderer.

Accumulates streamed tokens and re-renders them as live markdown so code blocks
and formatting appear as the assistant types.

v6.2.0: re-rendering the whole markdown buffer on *every* delta made long
answers quadratic (parse cost grows with length; delta count grows too). The
renderer now throttles to the Live display's refresh interval — tokens keep
accumulating at full stream speed, and the visible frame refreshes at most
once per ``_MIN_RENDER_INTERVAL_S``. The final buffer is always flushed when
the stream ends, so nothing is ever lost or truncated on screen.
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

# Match the UI's streaming Live refresh_per_second (15) — rendering faster
# than the display refreshes is wasted work.
_MIN_RENDER_INTERVAL_S = 1 / 15


class StreamRenderer:
    """Accumulates streamed tokens and re-renders them as live markdown."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._buffer = ""
        self._live: Live | None = None
        self._last_render = 0.0
        self._dirty = False

    def bind(self, live: Live) -> None:
        self._live = live

    def renderable(self):
        # Assistant output rendered as markdown for code blocks and formatting.
        return Markdown(self._buffer or "", code_theme="ansi_dark")

    def feed(self, chunk: str) -> None:
        self._buffer += chunk
        self._dirty = True
        now = time.monotonic()
        if self._live is not None and now - self._last_render >= _MIN_RENDER_INTERVAL_S:
            self._live.update(self.renderable())
            self._last_render = now
            self._dirty = False

    def flush(self) -> None:
        """Render any buffered-but-not-yet-shown tokens (final frame)."""
        if self._dirty and self._live is not None:
            self._live.update(self.renderable())
            self._last_render = time.monotonic()
            self._dirty = False

    @property
    def text(self) -> str:
        return self._buffer
