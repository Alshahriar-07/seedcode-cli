"""Desktop and Start Menu shortcuts for Seed Code (Windows, v9.2.1).

Neither of the two terminal install channels could produce a desktop icon:

* ``pip install`` has **no post-install hook**, so it installs the
  ``seedcode`` console script and stops there;
* the IRM installer (``install.ps1``) copies the standalone ``seedcode.exe``
  and updates ``PATH`` — it never created a shortcut either.

This module closes that gap for both. It writes a real ``.lnk`` through the
Windows Script Host COM object (``WScript.Shell``), so no third-party
dependency is involved, and it is a silent no-op on every other platform.

Two entry points:

* :func:`ensure_shortcuts` — create Desktop + Start Menu shortcuts now
  (used by ``seedcode --install-shortcuts`` and, on first launch, pip);
* :func:`first_run_shortcuts` — the one-time pip path: create the shortcuts
  on the first interactive Windows launch and record a marker so a shortcut
  the user deliberately deleted is never resurrected.

Nothing here raises into the caller: a shortcut failure must never stop the
application from starting.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

__all__ = [
    "SHORTCUT_NAME",
    "create_shortcut",
    "ensure_shortcuts",
    "first_run_shortcuts",
    "launch_spec",
    "shortcuts_supported",
]

#: The name shown on the Desktop / Start Menu (Windows appends ``.lnk``).
SHORTCUT_NAME = "Seed Code"

#: Opt-out for users (and CI) who do not want shortcuts created.
_DISABLE_ENV = "SEEDCODE_NO_SHORTCUTS"
#: Marker in the app directory: set once the first-run shortcut ran.
_MARKER_NAME = "shortcuts-created"
_TIMEOUT_S = 30.0
_DISABLED_VALUES = {"1", "true", "yes", "on"}


def shortcuts_supported() -> bool:
    """Whether this platform can have shortcuts at all (Windows only)."""
    return sys.platform == "win32"


def _disabled() -> bool:
    """Whether the user (or CI) opted out of shortcut creation."""
    return os.environ.get(_DISABLE_ENV, "").strip().lower() in _DISABLED_VALUES


def _powershell() -> str:
    """The PowerShell executable to use (Windows PowerShell is always present)."""
    return shutil.which("powershell") or shutil.which("pwsh") or "powershell"


def _run_powershell(script_body: str) -> subprocess.CompletedProcess | None:
    """Run a small PowerShell program from a temp file; None when it cannot run.

    A script file is used instead of ``-Command`` so paths containing spaces,
    quotes or ``$``/backtick characters cannot corrupt the command line.
    """
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".ps1", delete=False, encoding="utf-8", newline="\r\n"
        )
        handle.write(script_body)
        handle.close()
        return subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                handle.name,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    finally:
        if handle is not None:
            try:
                os.unlink(handle.name)
            except OSError:
                pass


def _ps(value: str) -> str:
    """A PowerShell single-quoted literal (doubling embedded quotes)."""
    return "'" + str(value).replace("'", "''") + "'"


def _shell_folders() -> tuple[Path | None, Path | None]:
    """(Desktop, Start Menu Programs) as Windows resolves them.

    Read through ``[Environment]::GetFolderPath`` so a OneDrive-redirected
    Desktop — very common — points at the folder the user actually sees
    instead of a hardcoded ``~/Desktop``.
    """
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8",
            "[Environment]::GetFolderPath('Desktop')",
            "[Environment]::GetFolderPath('Programs')",
        ]
    )
    result = _run_powershell(script)
    if result is None or result.returncode != 0:
        return None, None
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    desktop = Path(lines[0]) if lines else None
    programs = Path(lines[1]) if len(lines) > 1 else None
    return desktop, programs


def launch_spec() -> tuple[str, str]:
    """``(target, arguments)`` that really start *this* Seed Code install.

    A frozen/standalone ``seedcode.exe`` is launched directly. A pip install
    has a ``seedcode.exe`` console-script launcher in ``Scripts`` when one is
    on PATH; otherwise the current interpreter is used with ``-m seedcode``,
    which works in a virtualenv too.
    """
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 is not None and argv0.suffix.lower() == ".exe" and argv0.is_file():
        return str(argv0), ""
    found = shutil.which("seedcode")
    if found and Path(found).suffix.lower() == ".exe":
        return found, ""
    return sys.executable, "-m seedcode"


def create_shortcut(
    link: Path,
    target: str,
    *,
    arguments: str = "",
    icon: str = "",
    workdir: str = "",
    description: str = "Seed Code CLI",
) -> bool:
    """Write one real ``.lnk``; returns True only when the file exists.

    The icon defaults to the target's own resources, so the standalone exe
    shows the Seed Code icon it embeds.
    """
    if not shortcuts_supported():
        return False
    link = Path(link)
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$shell = New-Object -ComObject WScript.Shell",
            f"$shortcut = $shell.CreateShortcut({_ps(str(link))})",
            f"$shortcut.TargetPath = {_ps(target)}",
            f"$shortcut.Arguments = {_ps(arguments)}",
            f"$shortcut.WorkingDirectory = {_ps(workdir or str(Path(target).parent))}",
            f"$shortcut.Description = {_ps(description)}",
            f"$shortcut.IconLocation = {_ps(icon or f'{target},0')}",
            "$shortcut.Save()",
            "try { [Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null } catch { }",
        ]
    )
    result = _run_powershell(script)
    if result is None or result.returncode != 0:
        return False
    try:
        return link.is_file() and link.stat().st_size > 0
    except OSError:
        return False


def ensure_shortcuts(
    *,
    desktop: Path | None = None,
    programs: Path | None = None,
    force: bool = False,
    marker: Path | None = None,
    target: str | None = None,
    arguments: str | None = None,
) -> list[Path]:
    """Create the Desktop and Start Menu shortcuts; return the ones created.

    ``desktop``/``programs``/``marker`` are injectable so tests never touch
    the real desktop. Without ``force`` the marker suppresses re-creation, so
    a shortcut the user deleted stays deleted.
    """
    if not shortcuts_supported() or _disabled():
        return []

    if marker is None:
        try:
            from .helpers import app_dir

            marker = app_dir() / _MARKER_NAME
        except Exception:
            marker = Path.home() / ".seedcode" / _MARKER_NAME
    marker = Path(marker)
    if marker.exists() and not force:
        return []

    if target is None:
        target, resolved_arguments = launch_spec()
        arguments = resolved_arguments if arguments is None else arguments
    elif arguments is None:
        arguments = ""

    if desktop is None or programs is None:
        resolved_desktop, resolved_programs = _shell_folders()
        desktop = desktop if desktop is not None else resolved_desktop
        programs = programs if programs is not None else resolved_programs

    workdir = str(Path.home()) if arguments else str(Path(target).parent)
    created: list[Path] = []
    for directory in (desktop, programs):
        if directory is None:
            continue
        link = Path(directory) / f"{SHORTCUT_NAME}.lnk"
        if create_shortcut(
            link, target, arguments=arguments or "", workdir=workdir
        ):
            created.append(link)

    if created:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                "Desktop/Start Menu shortcuts created for Seed Code.\n",
                encoding="utf-8",
            )
        except OSError:
            pass
    return created


def first_run_shortcuts() -> list[Path]:
    """Create the shortcuts once, on the first interactive Windows launch.

    This is the pip path (pip cannot run anything after installing). It is
    deliberately conservative: only on Windows, only with a real console,
    only once, and never fatal.
    """
    if not shortcuts_supported() or _disabled():
        return []
    try:
        from .helpers import app_dir

        marker = app_dir() / _MARKER_NAME
        if marker.exists():
            return []
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return []
        return ensure_shortcuts(marker=marker)
    except Exception:  # a shortcut must never stop the app from starting
        return []
