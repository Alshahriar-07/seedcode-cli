"""Explicit workspace selection: the folder Seed Code is allowed to work in.

v9.1.0 replaces the old implicit behaviour ("wherever the terminal happened to
be, usually the user's home directory, becomes the project") with an explicit
choice made once at startup:

    Select Workspace

      1  Current Folder    D:\\Projects\\my-website
      2  Choose a Folder   browse this PC and pick any directory

The selected directory becomes the active **workspace** — the single root that
controls Agent Mode, file reads/writes, project indexing, ``.seedcode`` context
and ``plan.json``, terminal commands and verification. Nothing here invents a
workspace: the current folder is offered as-is, and "Choose a Folder" opens a
real folder picker for the local OS.

Automation (CI, piped hosts, scripts) never blocks on the prompt:

* ``SEEDCODE_WORKSPACE=<dir>`` selects the workspace directly;
* ``SEEDCODE_NO_WORKSPACE_PROMPT=1`` (or a non-interactive host) keeps the
  launch directory — the previous behaviour — without prompting.

Implementation note: the active workspace is applied with :func:`os.chdir`, so
every existing consumer (``PermissionManager``, the ``.seedcode`` store, the
indexer, ``run_command``'s ``cwd``, the header) reads the same live value
without a parallel source of truth.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

#: Explicit workspace (absolute or relative). Wins over the prompt.
WORKSPACE_ENV = "SEEDCODE_WORKSPACE"
#: Set to a truthy value to skip the prompt and keep the launch directory.
NO_PROMPT_ENV = "SEEDCODE_NO_WORKSPACE_PROMPT"

#: Choices returned by :func:`parse_choice`.
CURRENT = "current"
CHOOSE = "choose"

_TRUTHY = {"1", "true", "yes", "on"}


# --- active workspace ---------------------------------------------------------
def active_workspace() -> Path:
    """The directory Seed Code currently treats as the project workspace."""
    return Path.cwd()


def set_workspace(path: str | os.PathLike[str]) -> Path:
    """Make ``path`` the active workspace and return it (resolved).

    Raises :class:`NotADirectoryError` for a missing or non-directory target so
    a caller can report the problem instead of silently working in the wrong
    place.
    """
    resolved = Path(path).expanduser()
    if not resolved.is_dir():
        raise NotADirectoryError(f"not a directory: {resolved}")
    resolved = resolved.resolve()
    os.chdir(resolved)
    return resolved


def workspace_label(path: Path | None = None) -> str:
    """A short, displayable name for the workspace (its final component)."""
    target = Path(path) if path is not None else active_workspace()
    name = target.name
    return name or str(target)


# --- prompt text --------------------------------------------------------------
def menu_lines(cwd: Path | None = None) -> list[str]:
    """The "Select Workspace" menu, as plain lines (testable, no I/O)."""
    here = Path(cwd) if cwd is not None else active_workspace()
    return [
        "  Select Workspace",
        "  " + "-" * 62,
        "  Where should Seed Code work? Agent Mode reads, writes, indexes and",
        "  runs commands only inside the folder you pick.",
        "",
        f"    1  Current Folder    {here}",
        "    2  Choose a Folder   browse this PC and pick any directory",
        "",
    ]


def parse_choice(raw: str, cwd: Path | None = None) -> str | None:
    """Map a typed answer to a choice, a literal directory, or ``None``.

    ``""``/``1``/``current`` -> :data:`CURRENT`; ``2``/``choose`` -> 
    :data:`CHOOSE`; an existing directory path is accepted verbatim. Anything
    else returns ``None`` so the caller re-prompts instead of guessing.
    """
    text = (raw or "").strip()
    low = text.strip('"').strip("'").lower()
    if low in ("", "1", "current", "c", "here"):
        return CURRENT
    if low in ("2", "choose", "browse", "pick", "b"):
        return CHOOSE
    candidate = Path(text.strip('"').strip("'")).expanduser()
    if candidate.is_dir():
        return str(candidate)
    return None


# --- native folder pickers ----------------------------------------------------
def _pick_tkinter(initial: str | None = None) -> Path | None:
    """Tk's native directory dialog (present in most CPython installs)."""
    try:
        import tkinter  # noqa: PLC0415 — optional, platform-dependent
        from tkinter import filedialog
    except Exception:
        return None
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        chosen = filedialog.askdirectory(
            title="Select the Seed Code workspace folder",
            initialdir=initial or str(active_workspace()),
            mustexist=True,
        )
        return Path(chosen) if chosen else None
    except Exception:
        return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


_PS_FOLDER_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms | Out-Null\n"
    "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog\n"
    "$dialog.Description = 'Select the Seed Code workspace folder'\n"
    "$dialog.ShowNewFolderButton = $true\n"
    "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
    "{ Write-Output $dialog.SelectedPath }\n"
)


def _pick_powershell() -> Path | None:
    """Windows fallback that needs no Python GUI stack (a frozen EXE has none)."""
    if sys.platform != "win32":
        return None
    for exe in ("powershell", "pwsh"):
        binary = shutil.which(exe)
        if not binary:
            continue
        try:
            proc = subprocess.run(
                [binary, "-NoProfile", "-STA", "-Command", _PS_FOLDER_SCRIPT],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        chosen = (proc.stdout or "").strip()
        if chosen and Path(chosen).is_dir():
            return Path(chosen)
    return None


def _pick_posix() -> Path | None:
    """Linux/macOS pickers, in order of availability; ``None`` when absent."""
    if sys.platform == "darwin" and shutil.which("osascript"):
        try:
            proc = subprocess.run(
                ["osascript", "-e", "POSIX path of (choose folder)"],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        chosen = (proc.stdout or "").strip()
        return Path(chosen) if chosen and Path(chosen).is_dir() else None
    if sys.platform.startswith("linux"):
        for command in (
            ["zenity", "--file-selection", "--directory", "--title=Select the Seed Code workspace folder"],
            ["kdialog", "--getexistingdirectory", str(active_workspace())],
        ):
            if not shutil.which(command[0]):
                continue
            try:
                proc = subprocess.run(
                    command, capture_output=True, text=True, timeout=300
                )
            except (OSError, subprocess.SubprocessError):
                continue
            chosen = (proc.stdout or "").strip()
            if chosen and Path(chosen).is_dir():
                return Path(chosen)
    return None


def pick_folder_native(initial: Path | str | None = None) -> Path | None:
    """Open the OS folder picker; ``None`` when no picker is available.

    Tries the native GUI first (Tk on any platform, then a platform-specific
    helper). Never raises: a headless or stripped-down host simply gets
    ``None`` and the caller falls back to asking for a typed path.
    """
    start = str(initial) if initial is not None else None
    chosen = _pick_tkinter(start)
    if chosen is not None:
        return chosen
    chosen = _pick_posix()
    if chosen is not None:
        return chosen
    return _pick_powershell()


# --- the interactive flow -----------------------------------------------------
def select_workspace(
    *,
    cwd: Path | None = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    picker: Callable[..., Path | None] | None = None,
) -> Path:
    """Run the Select Workspace flow and return the chosen directory.

    ``Current Folder`` returns ``cwd``; ``Choose a Folder`` opens the native
    picker and falls back to a typed path when no GUI picker exists. EOF/^C
    (``EOFError``/``KeyboardInterrupt``) selects the current folder, so a
    mistyped answer can never leave the session without a workspace.
    """
    here = Path(cwd) if cwd is not None else active_workspace()
    pick = picker or pick_folder_native
    for line in menu_lines(here):
        print_fn(line)

    while True:
        try:
            answer = input_fn("  Select [1] > ")
        except (EOFError, KeyboardInterrupt):
            print_fn(f"  Keeping the current folder: {here}")
            return here

        choice = parse_choice(answer, here)
        if choice == CURRENT:
            return here
        if choice == CHOOSE:
            chosen = pick(here)
            if chosen is not None:
                return Path(chosen)
            print_fn(
                "  No folder picker is available on this host — type a path, "
                "or press Enter for the current folder."
            )
            try:
                typed = input_fn("  Folder path > ")
            except (EOFError, KeyboardInterrupt):
                return here
            resolved = parse_choice(typed, here)
            if resolved in (CURRENT, None):
                if resolved is CURRENT:
                    return here
                print_fn("  That folder does not exist.")
                continue
            return Path(resolved)
        if choice is not None:
            return Path(choice)
        print_fn("  Please answer 1, 2, or a path to an existing folder.")


def ensure_workspace(
    *,
    interactive: bool,
    cwd: Path | None = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    picker: Callable[..., Path | None] | None = None,
) -> Path:
    """Select the workspace at startup and apply it (never blocks automation).

    Order: an explicit ``SEEDCODE_WORKSPACE`` always wins; a non-interactive
    host or ``SEEDCODE_NO_WORKSPACE_PROMPT`` keeps the launch directory; every
    other run presents the Select Workspace menu.
    """
    here = Path(cwd) if cwd is not None else active_workspace()

    explicit = (os.environ.get(WORKSPACE_ENV) or "").strip()
    if explicit:
        try:
            return set_workspace(explicit)
        except (NotADirectoryError, OSError) as exc:
            print_fn(f"  {WORKSPACE_ENV} was ignored: {exc}")

    if not interactive or _no_prompt_requested():
        return here

    chosen = select_workspace(
        cwd=here, input_fn=input_fn, print_fn=print_fn, picker=picker
    )
    try:
        return set_workspace(chosen)
    except (NotADirectoryError, OSError) as exc:
        print_fn(f"  Could not use {chosen}: {exc}")
        return here


def _no_prompt_requested() -> bool:
    return (os.environ.get(NO_PROMPT_ENV) or "").strip().lower() in _TRUTHY


__all__ = [
    "CHOOSE",
    "CURRENT",
    "NO_PROMPT_ENV",
    "WORKSPACE_ENV",
    "active_workspace",
    "ensure_workspace",
    "menu_lines",
    "parse_choice",
    "pick_folder_native",
    "select_workspace",
    "set_workspace",
    "workspace_label",
]
