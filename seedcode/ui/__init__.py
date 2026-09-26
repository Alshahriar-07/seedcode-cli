"""Rich-based presentation layer for Seed Code.

Everything the user sees on screen is produced here so the visual identity —
the Seed theme system, the startup dashboard, panels, spinners, and the
interactive component library (selector, menus, dialogs, palette) — stays in
one place. Business logic lives elsewhere and calls into these helpers.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from typing import Iterator

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from .. import APP_NAME, TAGLINE, __version__
from ..core.models import AppConfig
from ..utils.text import safe_text
from .dashboard import render_dashboard
from .reference import render_command_hint
from .renderer import StreamRenderer
from .theme import SEED_THEME, rich_theme, set_active_theme

__all__ = ["UI", "StreamRenderer", "SEED_THEME"]


class UI:
    """Thin wrapper around a Rich console with Seed Code styling helpers."""

    #: True only for the persistent-TUI adapter (:class:`seedcode.ui.tui.TuiUI`),
    #: which owns the screen and replaces the Rich live displays.
    is_tui = False

    def __init__(self, plain: bool = False) -> None:
        # ``plain`` is the safe fallback for hosts that mangle ANSI (or when
        # the user sets SEEDCODE_PLAIN): no colour, no cursor tricks — Rich is
        # still used, it just emits nothing a terminal could corrupt.
        self.console = Console(
            theme=rich_theme(), highlight=False, no_color=plain
        )
        # The active Live display (spinner/stream), if any — permission
        # dialogs pause it so interactive input works cleanly.
        self._live: Live | None = None
        # Legacy Windows consoles (pre-Windows-Terminal cmd.exe with raster
        # fonts) can't render ✔/✖ — fall back to pure-ASCII markers there.
        if self.console.legacy_windows:
            self._ok_mark, self._err_mark = "[OK]", "[X]"
        else:
            self._ok_mark, self._err_mark = "✔", "✖"
        self._theme_pushed = False

    def apply_theme(self, name: str) -> None:
        """Switch the active theme everywhere (console + interactive styles).

        Keeps exactly one theme overlay on the console so live-preview
        arrowing through the picker never stacks themes.
        """
        set_active_theme(name)
        if self._theme_pushed:
            try:
                self.console.pop_theme()
            except Exception:
                pass
        self.console.push_theme(rich_theme(name))
        self._theme_pushed = True

    # --- primitives --------------------------------------------------------
    def print(self, *args, **kwargs) -> None:
        """Print to the console, with plain-string surrogates neutralised.

        v7.1.0: a lone surrogate anywhere in displayed text would raise
        ``UnicodeEncodeError: surrogates not allowed`` inside Rich. Strings are
        normalized here; Rich renderables (Text/Table/Panel) are left to their
        own construction, which is already normalized at the data boundary.
        """
        self._emit(
            *(safe_text(arg) if isinstance(arg, str) else arg for arg in args),
            **kwargs,
        )

    def _emit(self, *args, **kwargs) -> None:
        """Print through Rich, degrading to ASCII instead of ever raising.

        A console whose stream cannot encode a character (a raster-font cmd.exe
        on a legacy code page, a redirected cp1252 stream) makes Rich raise
        ``UnicodeEncodeError`` mid-task. That must never end a session, so the
        frame is re-rendered as plain text with unencodable characters
        replaced. Display code is never allowed to break the work.
        """
        try:
            self.console.print(*args, **kwargs)
            return
        except UnicodeError:
            pass
        except Exception:
            return  # a rendering bug must not stop a task either
        try:
            buffer = io.StringIO()
            probe = Console(
                file=buffer,
                width=self.console.size.width,
                legacy_windows=self.console.legacy_windows,
                no_color=True,
            )
            probe.print(*args, **kwargs)
            encoding = getattr(getattr(self.console, "file", None), "encoding", None)
            text = buffer.getvalue()
            if encoding:
                text = text.encode(encoding, "replace").decode(encoding, "replace")
            self.console.file.write(text)  # type: ignore[union-attr]
            self.console.file.flush()  # type: ignore[union-attr]
        except Exception:
            pass

    def blank(self) -> None:
        self._emit()

    # --- external live displays -------------------------------------------
    def register_live(self, live) -> None:
        """Let a component own the pausable Live display (the task view).

        Permission dialogs call :meth:`_confirm`, which stops ``self._live``
        while the user answers and restarts it afterwards, so an external
        display registers here to keep that behaviour.
        """
        self._live = live

    def unregister_live(self, live) -> None:
        """Release a display registered with :meth:`register_live`."""
        if self._live is live:
            self._live = None

    # --- startup -----------------------------------------------------------
    def banner(self, config: AppConfig) -> None:
        """Render the startup dashboard (shown exactly once at launch).

        The restored structured Seed Code dashboard — a bordered reference
        panel with the brand block, a divider and the live session values —
        plus one line of command hints. The ASCII logo is permanently gone;
        the brand is plain text. On a standard 80x24 terminal the panel and
        the prompt fit with room to spare.
        """
        try:
            render_dashboard(self.console, config)
            render_command_hint(self.console)
        except UnicodeError:
            # A console that cannot encode the panel glyphs still needs a
            # banner: degrade to plain text rather than failing startup.
            self._emit(f"{APP_NAME} CLI v{__version__} — {TAGLINE}")

    def statusbar(self, config: AppConfig) -> None:
        """One-line session summary: provider · model · mode · status.

        Used after mode switches, where re-rendering the whole dashboard would
        be noise. Never prints a value that is not in the live config.
        """
        from .dashboard import status_line

        self._emit(status_line(self.console, config))

    # --- chat rendering ----------------------------------------------------
    @contextmanager
    def thinking(self, label: str = "Thinking") -> Iterator[None]:
        """Show a spinner while awaiting the first streamed token."""
        spinner = Spinner("dots", text=Text(f" {label}...", style="seed.accent"))
        with Live(
            spinner, console=self.console, refresh_per_second=12, transient=True
        ) as live:
            self._live = live
            try:
                yield
            finally:
                self._live = None

    @contextmanager
    def streaming(self) -> Iterator["StreamRenderer"]:
        """Live markdown renderer; a console encoding failure degrades to text."""
        renderer = StreamRenderer(self.console)
        try:
            with Live(
                renderer.renderable(),
                console=self.console,
                refresh_per_second=15,
                transient=False,
            ) as live:
                renderer.bind(live)
                yield renderer
                # Final frame: show any tokens the throttle had not yet drawn.
                renderer.flush()
            self.console.print()
        except UnicodeError:
            # Legacy console that cannot encode the streamed text: emit it as
            # plain, unencodable characters replaced — never abort the answer.
            self._emit(renderer.text)
        finally:
            pass

    # --- messaging ---------------------------------------------------------
    def info(self, message: str) -> None:
        self._emit(Text(safe_text(message), style="seed.text"))

    def dim(self, message: str) -> None:
        self._emit(Text(safe_text(message), style="seed.dim"))

    def success(self, message: str) -> None:
        self._emit(Text(f"{self._ok_mark} {safe_text(message)}", style="seed.success"))

    def warning(self, message: str) -> None:
        self._emit(Text(f"! {safe_text(message)}", style="seed.warning"))

    def error(self, message: str) -> None:
        self._emit(Text(f"{self._err_mark} {safe_text(message)}", style="seed.error"))

    def panel(self, body, title: str | None = None) -> None:
        self._emit(
            Panel(
                body,
                title=title,
                border_style="seed.primary",
                title_align="left",
                padding=(1, 2),
            )
        )

    # --- action confirmation ------------------------------------------------
    def _confirm(self, title: str, category_label: str, description: str) -> str:
        """Ask the user to approve an action; returns 'y', 'a', or 'n'.

        Shows the action details in a warning panel, then an interactive
        Allow Once / Always Allow / Deny dialog. Pauses any live spinner so
        the dialog renders cleanly, then resumes it. Cancelling (Esc or
        Ctrl+C) counts as deny — never approve by accident.
        """
        from .dialog import permission_dialog

        live = self._live
        if live is not None:
            live.stop()
        try:
            body = Text()
            body.append(f"{safe_text(category_label)}\n", style="seed.warning")
            body.append(safe_text(description), style="seed.text")
            self.console.print(
                Panel(
                    body,
                    title=title,
                    border_style="seed.warning",
                    title_align="left",
                    padding=(1, 2),
                )
            )
            return permission_dialog()
        finally:
            if live is not None:
                live.start()

    def confirm_desktop(self, category_label: str, description: str) -> str:
        """Approve a desktop action; returns 'y', 'a', or 'n'."""
        return self._confirm("Desktop Control", category_label, description)

    def confirm_tool_action(self, category_label: str, description: str) -> str:
        """Approve a dangerous agent tool action; returns 'y', 'a', or 'n'."""
        return self._confirm("Agent Action", category_label, description)
