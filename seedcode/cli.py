"""Seed Code command-line entry point (``seedcode``).

Thin wrapper: builds the UI, hands off to the application controller
(:mod:`seedcode.app`), and provides the final safety net so a stray exception is
shown as a friendly message rather than a traceback.
"""

from __future__ import annotations

import sys

from .app import run
from .ui import UI


def main() -> None:
    """Console-script entry point (``seedcode``)."""
    # --version must work non-interactively (installers verify with it),
    # so handle it before any UI or config work happens.
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        from . import __version__

        print(f"Seed Code v{__version__}")
        return

    ui = UI()
    try:
        run(ui)
    except KeyboardInterrupt:
        ui.blank()
        ui.dim("Interrupted.")
    except Exception as exc:  # final safety net — never show a traceback
        ui.error(f"Fatal error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
