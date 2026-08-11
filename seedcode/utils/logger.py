"""Logging for Seed Code.

Seed Code is a quiet CLI: nothing is ever logged to the terminal. Instead a
rotating file under ``~/.seedcode/logs/seedcode.log`` records startup, config
loading, API request metadata and errors so problems can be diagnosed after
the fact. API keys and message content are never logged.

Set ``SEEDCODE_DEBUG=1`` to raise the file log level from INFO to DEBUG.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_ROOT = "seedcode"
_configured = False

# Keep the log small: two 512 KB files at most.
_MAX_BYTES = 512 * 1024
_BACKUP_COUNT = 1


def setup_logging() -> None:
    """Attach the rotating file handler once. Never raises, never prints."""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger(_ROOT)
    root.setLevel(logging.DEBUG)
    # Terminal stays silent even if no file handler could be attached.
    root.addHandler(logging.NullHandler())
    root.propagate = False

    try:
        # Imported here so importing this module stays dependency-light.
        from .helpers import app_dir

        log_dir = app_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "seedcode.log",
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        debug = os.environ.get("SEEDCODE_DEBUG", "").strip() not in ("", "0")
        handler.setLevel(logging.DEBUG if debug else logging.INFO)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    except OSError:
        # A read-only or full disk must never stop the app from starting.
        pass


def get_logger(name: str = _ROOT) -> logging.Logger:
    """Return a namespaced logger (silent until :func:`setup_logging` runs)."""
    if not name.startswith(_ROOT):
        name = f"{_ROOT}.{name}"
    return logging.getLogger(name)
