"""v9.3.2 startup and version regression tests.

The reported failure was::

    NameError: name 'ensure_workspace' is not defined

raised from ``seedcode/cli.py`` before any UI existed. The root cause was
missing imports in ``main()`` (``ensure_workspace`` and the terminal snapshot),
not a missing workspace function: ``ensure_workspace`` had always lived in
``seedcode/workspace.py``. These tests launch the real ``main()`` with only the
long-running app entry point replaced, so a missing import fails here instead of
on a user's first launch.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_main_selects_the_workspace_and_starts_the_app(monkeypatch, tmp_path) -> None:
    """``seedcode`` (no args) must reach the app without a NameError."""
    from seedcode import app, cli
    from seedcode.utils import shortcuts

    monkeypatch.setenv("SEEDCODE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("SEEDCODE_NO_TUI", "1")
    monkeypatch.setattr(sys, "argv", ["seedcode"])
    monkeypatch.setattr(shortcuts, "first_run_shortcuts", lambda: [])

    started: list[object] = []
    monkeypatch.setattr(app, "run", lambda ui: started.append(ui))
    monkeypatch.setattr(app, "run_persistent", lambda: started.append("persistent"))

    original = os.getcwd()
    try:
        cli.main()
    finally:
        os.chdir(original)

    # The sequential console is used here (pytest has no TTY / NO_TUI is set),
    # and exactly one UI was built and handed to the app controller.
    assert len(started) == 1
    assert started[0] != "persistent"


def test_startup_helpers_are_imported_where_they_are_used() -> None:
    """A source-level guard: both symbols must be imported in ``cli.py``."""
    source = (ROOT / "seedcode" / "cli.py").read_text(encoding="utf-8")
    assert "from .workspace import ensure_workspace" in source
    assert "from .utils.terminal_env import detect_terminal" in source
    # The snapshot must be created before the call site that consumes it.
    assert source.index("terminal = detect_terminal()") < source.index(
        "if _use_persistent_tui(terminal.plain):"
    )


def test_module_entry_point_reports_931() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "seedcode", "--version"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "Seed Code CLI 9.3.2"


def test_version_agrees_across_the_distribution_channels() -> None:
    from seedcode import __version__

    assert __version__ == "9.3.2"

    npm = json.loads((ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
    assert npm["version"] == __version__

    ps = (ROOT / "IRM_INSTALL" / "install.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "IRM_INSTALL" / "install.sh").read_text(encoding="utf-8")
    iss = (ROOT / "scripts" / "windows" / "setup.iss").read_text(encoding="utf-8")
    assert '$Version = "9.3.2"' in ps
    assert 'DEFAULT_VERSION="9.3.2"' in sh
    assert '#define MyAppVersion "9.3.2"' in iss
