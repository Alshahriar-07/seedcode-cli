"""Seed Code command-line entry point (``seedcode``).

Thin wrapper: prepares the Windows console, builds the UI, hands off to the
application controller (:mod:`seedcode.app`), and provides the final safety
net so a stray exception is shown as a friendly message rather than a
traceback. Heavy imports are deferred into :func:`main` so ``--version``
(used by installers to verify the install) answers instantly.
"""

from __future__ import annotations

import os
import sys


def _interactive() -> bool:
    """True when a real console is attached (the TUI needs one to draw)."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _use_persistent_tui(plain: bool) -> bool:
    """Whether to start the persistent TUI instead of the sequential console.

    The TUI owns the whole screen, so it is only used on a real interactive
    console, never when the host mangles ANSI (``SEEDCODE_PLAIN``) or the user
    opts out with ``SEEDCODE_NO_TUI``.
    """
    if plain or os.environ.get("SEEDCODE_NO_TUI"):
        return False
    return _interactive()


def _prepare_console() -> None:
    """Make stdout/stderr UTF-8-safe, primarily for Windows.

    Interactive Windows consoles accept Unicode natively (PEP 528), but a
    redirected stream falls back to the legacy ANSI code page (e.g. cp1252),
    where the banner's box-drawing characters would raise UnicodeEncodeError.
    Reconfiguring to UTF-8 with ``errors="replace"`` makes output safe in
    Windows Terminal, PowerShell, CMD, and when piped to files.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass  # exotic streams (tests, embedders) — never fatal


_HELP = """\
Seed Code - a terminal-based AI coding assistant. Plant ideas. Grow code.

Usage:
  seedcode                     Start the interactive app (chat + menu)
  seedcode --version           Print the version and exit
  seedcode --help              Show this help and exit
  seedcode --install-shortcuts Create Desktop + Start Menu shortcuts (Windows)

Inside the app, type /help for commands: /provider, /model, /agent,
/permission, /settings, /doctor, and more.

Docs: https://github.com/Alshahriar-07/seedcode-cli
"""


def main() -> None:
    """Console-script entry point (``seedcode``)."""
    # --version/--help must work non-interactively and fast (installers and
    # scripts verify with them), so handle both before any UI, config, or
    # logging work happens.
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        from . import __version__

        # Installers verify the install by parsing this exact line.
        print(f"Seed Code CLI {__version__}")
        return
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print(_HELP)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--install-shortcuts":
        # v9.2.1: pip installs have no post-install hook, and the IRM installer
        # is a bare exe copy — this is the explicit way to (re)create the
        # Desktop / Start Menu shortcuts, on any Windows install channel.
        from .utils.shortcuts import ensure_shortcuts, shortcuts_supported

        if not shortcuts_supported():
            print("Desktop shortcuts are only supported on Windows.")
            return
        created = ensure_shortcuts(force=True)
        if created:
            for path in created:
                print(f"Created shortcut: {path}")
            return
        # Deliberately NOT sys.exit: cli.py owns exactly one exit path (the
        # fatal-error handler below), and that invariant is enforced by the
        # task-flow tests. A failed shortcut is reported, never fatal.
        print(
            "Could not create the shortcuts. Check that PowerShell is available, "
            "then retry: seedcode --install-shortcuts"
        )

    _prepare_console()

    # v9.3.1: the terminal snapshot and the workspace selector are imported
    # here (deferred, like every other heavy import) and were previously
    # referenced without being imported at all. That missing import was the
    # real cause of `NameError: name 'ensure_workspace' is not defined` — the
    # workspace was never selected, so startup failed before the UI existed.
    from .utils.terminal_env import detect_terminal
    from .utils.logger import get_logger, setup_logging
    from .workspace import ensure_workspace

    setup_logging()
    log = get_logger("cli")

    from . import __version__
    from .utils.terminal_title import (
        restore_terminal_title,
        seedcode_cli_title,
        set_terminal_title,
    )

    # v9.2.0: claim the terminal tab title once, from the CLI lifecycle —
    # never from a UI render pass — and restore the original on exit. A
    # title-less environment (pipes, tests, CI) is a silent no-op inside.
    # --version/--help above return before this, so installers that parse
    # output are never touched.
    set_terminal_title(seedcode_cli_title(__version__))

    # v9.2.1: a pip install never runs a post-install step, so the Desktop and
    # Start Menu shortcuts are created once on the first interactive Windows
    # launch (marker-guarded, no-op elsewhere, never fatal).
    from .utils.shortcuts import first_run_shortcuts

    for shortcut in first_run_shortcuts():
        log.info("created shortcut: %s", shortcut)

    terminal = detect_terminal()
    log.info("terminal: %s", terminal.describe())
    try:
        # v9.1.0: pick the project workspace explicitly instead of silently
        # treating the launch directory as the project. Non-interactive hosts
        # (pipelines, CI, scripts) skip the prompt and keep the launch
        # directory; `SEEDCODE_WORKSPACE` selects one directly. Everything
        # below — Agent Mode, file I/O, .seedcode, indexing, terminal
        # commands, verification — resolves against whatever this returns.
        workspace = ensure_workspace(interactive=_interactive())
        log.info("workspace selected: %s", workspace)

        from .app import run
        from .ui import UI

        ui = UI(plain=terminal.plain)
        try:
            if _use_persistent_tui(terminal.plain):
                # v9.1.0: a real console gets the persistent three-region
                # TUI; every other host keeps the sequential console.
                from .app import run_persistent

                run_persistent()
            else:
                run(ui)
            log.info("Clean exit.")
        except KeyboardInterrupt:
            ui.blank()
            ui.dim("Interrupted.")
            log.info("Exited via Ctrl+C.")
        except Exception as exc:  # final safety net — never show a traceback
            log.exception("Fatal error")
            ui.error(f"Fatal error: {exc}")
            ui.dim("Details were logged to ~/.seedcode/logs/seedcode.log")
            sys.exit(1)
    finally:
        restore_terminal_title()


if __name__ == "__main__":
    main()