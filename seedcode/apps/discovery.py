"""Application discovery across trusted Windows sources.

The ladder, cheapest and most trustworthy first (all injectable):

1. **Start Menu shortcuts** (``.lnk``) — the canonical user-visible app list;
   both per-user and all-users Start Menus are scanned.
2. **App Paths registry** — per-machine/per-user registered executables
   (``HKLM\\...\\App Paths\\spotify.exe``).
3. **Uninstall registrations** — installed-app metadata (name, install
   location, display icon), the same source "Add/Remove Programs" reads.
4. **PATH lookup** — ``shutil.which`` for CLI tools and portable apps.

Every source is matched case-insensitively on the requested name; the
requester gets one :class:`AppInfo` with the safest available launch target.
Non-Windows platforms return "not found" rather than guessing.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import ApplicationNotFoundError


@dataclass(slots=True)
class AppInfo:
    """One discovered application."""

    name: str
    source: str                    # start_menu | app_paths | uninstall | path
    target: str = ""               # shortcut path or executable
    exe: str = ""                  # resolved executable path ("" when unknown)
    install_location: str = ""
    pid: int = 0                   # set by the launcher/verifier, not discovery
    matched: str = ""              # which discovery key matched

    def describe(self) -> str:
        via = f" via {self.source}" if self.source else ""
        return f'"{self.name}"{via}'


# Directory-name fragments for the two Start Menu program folders.
_START_MENU_DIRS = (
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
)


def _iter_start_menu() -> list[Path]:
    shortcuts: list[Path] = []
    for base in _START_MENU_DIRS:
        try:
            if base.is_dir():
                shortcuts.extend(base.rglob("*.lnk"))
        except OSError:
            continue
    return shortcuts


def _shortcut_display_name(path: Path) -> str:
    """A .lnk file's user-visible name: its stem (no resolution needed)."""
    return path.stem.strip()


def _match_name(haystack: str, needle: str) -> bool:
    """Case-insensitive whole-word-ish containment match."""
    h = " ".join(haystack.lower().split())
    n = " ".join(needle.lower().split())
    return bool(n) and (n == h or n in h)


# --- source 2: App Paths -----------------------------------------------------------
def _app_paths() -> dict[str, AppInfo]:
    """``App Paths`` registrations: exe name -> full path (best-effort)."""
    found: dict[str, AppInfo] = {}
    if os.name != "nt":
        return found
    try:
        import winreg

        for hive, flag in ((winreg.HKEY_LOCAL_MACHINE, 0), (winreg.HKEY_CURRENT_USER, 0)):
            try:
                root = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths", 0, winreg.KEY_READ | flag)
            except OSError:
                continue
            with root:
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(root, subkey_name) as sub:
                            exe_path, _t = winreg.QueryValueEx(sub, "")
                    except OSError:
                        continue
                    name = Path(subkey_name).stem
                    found[name.lower()] = AppInfo(
                        name=name, source="app_paths",
                        target=str(exe_path or ""), exe=str(exe_path or ""),
                        matched=subkey_name,
                    )
    except ImportError:
        pass
    return found


# --- source 3: uninstall registrations ----------------------------------------------
def _uninstall_apps() -> dict[str, AppInfo]:
    """Display-name/install-location entries from the uninstall keys."""
    found: dict[str, AppInfo] = {}
    if os.name != "nt":
        return found
    try:
        import winreg

        paths = (
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        )
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for subpath in paths:
                try:
                    root = winreg.OpenKey(hive, subpath, 0, winreg.KEY_READ)
                except OSError:
                    continue
                with root:
                    i = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(root, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(root, subkey_name) as sub:
                                display, _t = winreg.QueryValueEx(sub, "DisplayName")
                                try:
                                    loc, _t2 = winreg.QueryValueEx(sub, "InstallLocation")
                                except OSError:
                                    loc = ""
                        except OSError:
                            continue
                        name = str(display or "").strip()
                        if not name:
                            continue
                        entry = AppInfo(
                            name=name, source="uninstall",
                            install_location=str(loc or ""),
                            matched=subkey_name,
                        )
                        found.setdefault(name.lower(), entry)
    except ImportError:
        pass
    return found


# --- the discovery facade -------------------------------------------------------------
def find_app(name: str, *, start_menu: list[Path] | None = None) -> AppInfo:
    """Locate an installed application by (fuzzy) name.

    ``start_menu`` injects the shortcut list for tests. Raises
    :class:`ApplicationNotFoundError` when every source misses.
    """
    wanted = (name or "").strip()
    if not wanted:
        raise ApplicationNotFoundError("An application name is required.")

    # 1) Start Menu shortcuts — the canonical list. Prefer exact stem matches.
    shortcuts = _iter_start_menu() if start_menu is None else start_menu
    exact: Path | None = None
    partial: Path | None = None
    for path in shortcuts:
        display = _shortcut_display_name(path)
        if display.lower() == wanted.lower():
            exact = path
            break
        if partial is None and _match_name(display, wanted):
            partial = path
    chosen = exact or partial
    if chosen is not None:
        return AppInfo(
            name=_shortcut_display_name(chosen), source="start_menu",
            target=str(chosen), matched=str(chosen),
        )

    # 2) App Paths (spotify.exe -> full path).
    for app_name, info in _app_paths().items():
        if _match_name(app_name, wanted):
            return info

    # 3) Uninstall registrations (covers apps without Start Menu entries).
    for app_name, info in _uninstall_apps().items():
        if _match_name(app_name, wanted):
            # Uninstall entries know the location but not the exe; a launch
            # attempt will resolve the target from the install location.
            if info.install_location:
                loc = Path(info.install_location)
                for candidate in (wanted.lower(), wanted.lower().replace(" ", "")):
                    exe = loc / f"{candidate}.exe"
                    if exe.is_file():
                        info.exe = str(exe)
                        info.target = str(exe)
                        break
            return info

    # 4) PATH — CLI tools and portable apps.
    which = shutil.which(wanted)
    if which:
        return AppInfo(
            name=wanted, source="path", target=which, exe=which, matched=which
        )

    raise ApplicationNotFoundError(
        f'"{wanted}" is not installed (searched Start Menu, App Paths, '
        "installed-app registrations, and PATH)."
    )


def installed_apps(*, start_menu: list[Path] | None = None) -> list[AppInfo]:
    """Every discoverable app (Start Menu + App Paths), name-deduplicated.

    Used by memory and by "what can I open?" queries; not for matching.
    """
    seen: dict[str, AppInfo] = {}
    shortcuts = _iter_start_menu() if start_menu is None else start_menu
    for path in shortcuts:
        display = _shortcut_display_name(path)
        if display and display.lower() not in seen:
            seen[display.lower()] = AppInfo(
                name=display, source="start_menu", target=str(path), matched=str(path)
            )
    for key, info in _app_paths().items():
        seen.setdefault(key, info)
    return sorted(seen.values(), key=lambda a: a.name.lower())
