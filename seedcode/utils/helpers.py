"""Small shared helpers for Seed Code.

Kept dependency-light so importing this module stays cheap during startup.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path


def app_dir() -> Path:
    """Return the per-user Seed Code directory, creating it if needed.

    Uses ``~/.seedcode`` on every platform for predictable, cross-platform
    behaviour (Windows PowerShell, Linux, macOS). If the home directory is
    unwritable (locked-down corporate machines), falls back to a temp
    location so the app still starts.
    """
    path = Path.home() / ".seedcode"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path(tempfile.gettempdir()) / "seedcode"
        path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_path(*parts: str) -> Path:
    """Resolve a read-only asset that ships with Seed Code.

    Works in both modes the app runs in:

    * **frozen** (the PyInstaller one-file exe) — data files are unpacked to a
      temp directory exposed as ``sys._MEIPASS``;
    * **source / pip** — assets sit inside the installed ``seedcode`` package.

    The path is returned whether or not it exists; callers decide what a
    missing asset means.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", "") or Path(sys.executable).parent)
    else:
        base = Path(__file__).resolve().parents[1]  # -> seedcode/
    return base.joinpath(*parts)


def install_dir() -> Path:
    """The directory Seed Code is installed in (next to the exe when frozen).

    Distinct from :func:`bundled_path`: the Windows installer places large
    payloads beside ``seedcode.exe`` rather than inside it, so they survive as
    real files instead of being unpacked on every launch.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    """Path to the JSON configuration file."""
    return app_dir() / "config.json"


def history_dir(provider_id: str = "") -> Path:
    """Directory holding saved conversation transcripts.

    Each provider keeps its own history under ``history/<provider_id>/`` so
    switching backends never mixes conversations.
    """
    path = app_dir() / "history"
    if provider_id:
        path = path / provider_id
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # history is best-effort; callers already tolerate write failures
    return path


def restrict_permissions(path: Path) -> None:
    """Best-effort: make a file readable/writable by the owner only.

    On Windows this is a no-op (POSIX perms are ignored) but it never raises,
    so callers can invoke it unconditionally.
    """
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass


def session_id() -> str:
    """Generate a filesystem-safe id for a chat session."""
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())
