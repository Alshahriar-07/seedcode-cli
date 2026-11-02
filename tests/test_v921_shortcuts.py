"""v9.2.1 Windows shortcut tests.

The reported bug: installing Seed Code from the IRM terminal installer — or
with ``pip install`` — left no desktop icon. ``install.ps1`` only copied the
exe and ``pip`` has no post-install hook, so neither channel ever called a
shortcut API.

These tests execute the REAL shortcut path: a ``.lnk`` is written through the
Windows Script Host COM object into a temporary directory (never the user's
real Desktop), then checked on disk. The installer script is asserted
statically, because ISCC and a full PowerShell install cannot run here.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from seedcode.utils import shortcuts
from seedcode.utils.shortcuts import (
    create_shortcut,
    ensure_shortcuts,
    launch_spec,
    shortcuts_supported,
)

ROOT = Path(__file__).resolve().parent.parent
PS_INSTALLER = ROOT / "IRM_INSTALL" / "install.ps1"
ISS_INSTALLER = ROOT / "scripts" / "windows" / "setup.iss"

WINDOWS = sys.platform == "win32"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
requires_windows = pytest.mark.skipif(
    not (WINDOWS and POWERSHELL), reason="Windows + PowerShell required"
)


def test_shortcut_support_is_windows_only(monkeypatch) -> None:
    assert shortcuts_supported() is WINDOWS
    monkeypatch.setattr(sys, "platform", "linux")
    assert shortcuts_supported() is False
    assert ensure_shortcuts(force=True) == []


@requires_windows
def test_create_shortcut_writes_a_real_lnk(tmp_path: Path) -> None:
    link = tmp_path / "Seed Code.lnk"
    assert create_shortcut(link, sys.executable, arguments="-c pass", workdir=str(tmp_path))
    assert link.is_file() and link.stat().st_size > 0


@requires_windows
def test_ensure_shortcuts_creates_desktop_and_start_menu_once(tmp_path: Path) -> None:
    desktop = tmp_path / "Desktop"
    programs = tmp_path / "Programs"
    desktop.mkdir()
    programs.mkdir()
    marker = tmp_path / "shortcuts-created"
    console = Path(shutil.which("cmd") or sys.executable)

    created = ensure_shortcuts(
        desktop=desktop,
        programs=programs,
        marker=marker,
        target=str(console),
    )
    assert len(created) == 2
    assert (desktop / "Seed Code.lnk").is_file()
    assert (programs / "Seed Code.lnk").is_file()
    assert marker.is_file()

    # A shortcut the user deleted must NOT come back on the next start.
    (desktop / "Seed Code.lnk").unlink()
    assert ensure_shortcuts(desktop=desktop, programs=programs, marker=marker) == []
    assert not (desktop / "Seed Code.lnk").exists()

    # ...but the explicit flag (force) restores it.
    forced = ensure_shortcuts(
        desktop=desktop, programs=programs, marker=marker, force=True, target=str(console)
    )
    assert len(forced) == 2
    assert (desktop / "Seed Code.lnk").is_file()


@requires_windows
def test_shortcut_creation_can_be_disabled_by_env(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SEEDCODE_NO_SHORTCUTS", "1")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    assert ensure_shortcuts(
        desktop=desktop,
        programs=desktop,
        marker=tmp_path / "marker",
        target=sys.executable,
    ) == []
    assert not (desktop / "Seed Code.lnk").exists()


def test_launch_spec_targets_a_real_launcher(tmp_path: Path) -> None:
    target, arguments = launch_spec()
    assert target
    if arguments == "-m seedcode":
        assert Path(target).exists()
    else:
        assert Path(target).suffix.lower() == ".exe"

    # A frozen/standalone run launches the exe itself, with no arguments.
    fake_exe = tmp_path / "seedcode.exe"
    fake_exe.write_bytes(b"MZ fake")
    original = sys.argv
    try:
        sys.argv = [str(fake_exe)]
        assert launch_spec() == (str(fake_exe), "")
    finally:
        sys.argv = original


# --- the installer must actually call the shortcut API -----------------------
def test_irm_installer_creates_shortcuts_by_default() -> None:
    text = PS_INSTALLER.read_text(encoding="utf-8")
    assert "$shortcut.TargetPath" in text
    assert "CreateShortcut" in text
    assert "GetFolderPath(\"Desktop\")" in text
    assert "GetFolderPath(\"Programs\")" in text
    # Created by default, opt-out available; both the fresh-install and the
    # already-installed/upgrade paths must call it.
    assert "[switch] $NoShortcuts" in text
    assert "Install-Shortcuts $TargetExe" in text
    assert text.count("Install-Shortcuts $TargetExe") >= 2


def test_setup_installer_desktop_icon_is_not_opt_in() -> None:
    text = ISS_INSTALLER.read_text(encoding="utf-8")
    task = [
        line
        for line in text.splitlines()
        if line.startswith("Name: \"desktopicon\"")
    ]
    assert task, "desktopicon task missing"
    # The task may no longer carry the old unchecked flag.
    assert "unchecked" not in "\n".join(
        text.split("[Tasks]", 1)[1].split("[Files]", 1)[0]
    )
    assert "Tasks: desktopicon" in text


def test_cli_exposes_the_shortcut_flag() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "seedcode", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "--install-shortcuts" in result.stdout
