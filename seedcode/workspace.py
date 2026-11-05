"""Explicit workspace selection: the folder Seed Code is allowed to work in.

v9.1.0 replaces the old implicit behaviour ("wherever the terminal happened to
be, usually the user's home directory, becomes the project") with an explicit
choice made once at startup with the arrow-key selector:

    Current Workspace
    Browse Folders
    Recent Workspaces

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
        "Workspace Selection",
        f"Current workspace: {here}",
        "Choose with the arrow keys or enter a directory path.",
    ]


def parse_choice(raw: str, cwd: Path | None = None) -> str | None:
    """Map a typed answer to a choice, a literal directory, or ``None``.

    ``""``/``current`` -> :data:`CURRENT`; ``choose`` -> 
    :data:`CHOOSE`; an existing directory path is accepted verbatim. Anything
    else returns ``None`` so the caller re-prompts instead of guessing.
    """
    text = (raw or "").strip()
    low = text.strip('"').strip("'").lower()
    if low in ("", "current", "c", "here"):
        return CURRENT
    if low in ("choose", "browse", "pick", "b"):
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
    """Run the interactive folder selector or a scriptable path prompt."""
    here = Path(cwd) if cwd is not None else active_workspace()
    pick = picker or pick_folder_native
    if input_fn is input and _has_interactive_tty():
        return _select_workspace_menu(here, pick)
    for line in menu_lines(here):
        print_fn(line)

    while True:
        try:
            answer = input_fn("  Workspace path (Enter keeps current) > ")
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
                "  No folder picker is available; type a directory path or press Enter."
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
        print_fn("  Enter an existing directory path, or press Enter to keep the current folder.")


def _has_interactive_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _select_workspace_menu(here: Path, picker: Callable[..., Path | None]) -> Path:
    from .ui.menu import MenuItem, run_menu
    from .ui.textbox import read_text

    while True:
        try:
            from .config.manager import load_config

            recent = list(load_config().recent_workspaces)
        except Exception:
            recent = []
        choice = run_menu(
            [
                MenuItem("Use current workspace", "current", status=str(here)),
                MenuItem("Browse folders", "browse"),
                MenuItem("Recent workspaces", "recent", disabled=not recent),
                MenuItem("Choose with system folder picker", "native"),
                MenuItem("Open directory by path", "path"),
                MenuItem("Back", "back"),
            ],
            title="Workspace Selection",
            breadcrumbs=("Workspace",),
            hint="Up/Down navigate   Enter select   Esc back",
        )
        if choice in (None, "back"):
            return here
        if choice == "current":
            return here
        if choice == "browse":
            selected = _browse_workspace(here, run_menu, MenuItem)
            if selected is not None:
                return selected
        elif choice == "recent":
            selected = run_menu(
                [MenuItem(path, path) for path in recent] + [MenuItem("Back", "back")],
                title="Recent Workspaces",
                hint="Up/Down navigate   Enter select   Esc back",
            )
            if selected and selected != "back" and Path(selected).is_dir():
                return Path(selected)
        elif choice == "native":
            selected = picker(here)
            if selected is not None:
                return Path(selected)
        elif choice == "path":
            typed = read_text("Directory path > ")
            if typed:
                candidate = Path(typed).expanduser()
                if candidate.is_dir():
                    return candidate


def _browse_workspace(here: Path, run_menu, menu_item) -> Path | None:
    current = here
    while True:
        try:
            entries = sorted(
                (entry for entry in current.iterdir() if entry.is_dir()),
                key=lambda entry: entry.name.casefold(),
            )
        except OSError:
            entries = []
        items = [menu_item("Select this folder", f"select:{current}")]
        if current.parent != current:
            items.append(menu_item("Go to parent directory", f"browse:{current.parent}"))
        if os.name == "nt":
            drives = [Path(f"{letter}:\\") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
            items.extend(
                menu_item(f"Browse {drive}", f"browse:{drive}")
                for drive in drives
                if drive.is_dir()
            )
        items.extend(menu_item(f"{entry.name}\\", f"browse:{entry}") for entry in entries)
        items.append(menu_item("Back", "back"))
        choice = run_menu(
            items,
            title="Select Directory",
            breadcrumbs=("Workspace", str(current)),
            hint="Up/Down navigate   Enter open/select   / filters   Esc back",
        )
        if choice in (None, "back"):
            return None
        kind, _, value = str(choice).partition(":")
        if kind == "select":
            return Path(value)
        if kind == "browse" and Path(value).is_dir():
            current = Path(value)


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
        selected = set_workspace(chosen)
        try:
            from .config.manager import load_config, save_config

            config = load_config()
            config.recent_workspaces = list(
                dict.fromkeys([str(selected), *config.recent_workspaces])
            )[:12]
            save_config(config)
        except Exception:
            pass
        return selected
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
