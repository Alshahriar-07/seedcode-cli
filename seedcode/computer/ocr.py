"""OCR provisioning: the text-recognition tier, configured for the user.

OCR used to be the weakest link in the detection ladder. ``pytesseract`` is
only a thin wrapper around the ``tesseract`` executable, and nothing shipped
that executable — so on a normal machine the OCR tier silently did nothing, and
a workflow that reached it failed with "OCR is not installed". Worse, the old
availability check asked only whether the *Python wrapper* imported, so Seed
Code would report OCR as available while every call to it failed.

This module fixes both halves:

* :func:`tesseract_path` finds the engine across every way Seed Code can be
  installed — bundled next to the frozen exe, unpacked inside it, dropped in
  the user's data directory, or already on PATH — and :func:`configure` points
  ``pytesseract`` at whatever it found.
* :func:`available` reports the truth: the wrapper **and** a working binary.

The user is never asked to install anything: the Windows installer ships the
engine (see ``scripts/windows/setup.iss``) and :func:`configure` runs
automatically the first time the OCR tier is used.

Deterministic and offline. No AI, and no network access — Seed Code never
downloads a dependency behind the user's back.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..utils.helpers import app_dir, bundled_path, install_dir

# Where the engine lives relative to each install shape. Ordered by
# trustworthiness: something we shipped beats whatever happens to be on PATH,
# so behaviour cannot drift with an unrelated system install.
_SUBDIR = "tesseract"
_EXE = "tesseract.exe" if os.name == "nt" else "tesseract"

# Env var lets a user or CI pin a specific engine explicitly.
_ENV_VARS = ("SEEDCODE_TESSERACT", "TESSERACT_CMD")

# Probing the binary costs a subprocess launch; the answer cannot change within
# a session, so it is computed once.
_probe_cache: "tuple[str, bool] | None" = None


def candidate_paths() -> list[Path]:
    """Every location the engine may live, in resolution order."""
    paths: list[Path] = []
    for var in _ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            paths.append(Path(value))
    # Beside the installed exe (how the Windows installer ships it).
    paths.append(install_dir() / _SUBDIR / _EXE)
    # Unpacked from the frozen bundle, or inside the source package.
    paths.append(bundled_path(_SUBDIR, _EXE))
    # The user's own data directory — the sideload location.
    paths.append(app_dir() / _SUBDIR / _EXE)
    if os.name == "nt":
        # A normal system-wide Tesseract install.
        for root in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            if root:
                paths.append(Path(root) / "Tesseract-OCR" / _EXE)
    return paths


def tesseract_path() -> str | None:
    """The path to a usable tesseract executable, or None if there is none."""
    for path in candidate_paths():
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    found = shutil.which("tesseract")
    return found or None


def tessdata_path(exe: str | None = None) -> str | None:
    """The language-data directory matching ``exe``, if it ships alongside."""
    if exe is None:
        exe = tesseract_path()
    if not exe:
        return None
    candidate = Path(exe).parent / "tessdata"
    try:
        return str(candidate) if candidate.is_dir() else None
    except OSError:
        return None


def configure() -> str | None:
    """Point ``pytesseract`` at the resolved engine. Returns the path used.

    Safe and cheap to call repeatedly — every OCR entry point calls it first so
    the engine is configured on first use with no setup step from the user.
    """
    exe = tesseract_path()
    if not exe:
        return None
    try:
        import pytesseract
    except ImportError:
        return None
    pytesseract.pytesseract.tesseract_cmd = exe
    # Bundled builds need TESSDATA_PREFIX or the engine cannot find eng.traineddata.
    data = tessdata_path(exe)
    if data and not os.environ.get("TESSDATA_PREFIX"):
        os.environ["TESSDATA_PREFIX"] = data
    return exe


def wrapper_installed() -> bool:
    """Whether the ``pytesseract`` Python package is importable."""
    import importlib.util

    return importlib.util.find_spec("pytesseract") is not None


def engine_works(exe: str | None = None) -> bool:
    """Whether the engine actually runs (cached for the session).

    A file existing is not proof it executes — a partial install, a blocked
    binary, or a missing VC runtime all produce a present-but-broken exe. The
    honest check is to run it.
    """
    global _probe_cache
    if exe is None:
        exe = tesseract_path()
    if not exe:
        return False
    if _probe_cache is not None and _probe_cache[0] == exe:
        return _probe_cache[1]
    ok = False
    try:
        proc = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        ok = proc.returncode == 0 and "tesseract" in (proc.stdout + proc.stderr).lower()
    except (OSError, subprocess.SubprocessError):
        ok = False
    _probe_cache = (exe, ok)
    return ok


def available() -> bool:
    """Whether OCR can genuinely run: wrapper present *and* engine working."""
    if not wrapper_installed():
        return False
    return engine_works(configure())


def status() -> tuple[bool, str]:
    """(ok, human-readable detail) for /doctor and the capability tables."""
    if not wrapper_installed():
        return False, (
            "the pytesseract package is missing — reinstall Seed Code, or run: "
            "pip install seedcode-cli[desktop]"
        )
    exe = configure()
    if not exe:
        return False, (
            "the Tesseract engine was not found. It ships with the Seed Code "
            "installer; reinstall, or set SEEDCODE_TESSERACT to a tesseract "
            f"executable. Looked in: {_searched()}"
        )
    if not engine_works(exe):
        return False, f"found {exe} but it did not run (damaged or blocked install)"
    return True, exe


def version() -> str:
    """The engine's reported version, or an empty string."""
    exe = tesseract_path()
    if not exe:
        return ""
    try:
        proc = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    first = (proc.stdout or proc.stderr or "").strip().splitlines()
    return first[0] if first else ""


def reset_cache() -> None:
    """Forget the probe result (tests, and after an install repairs itself)."""
    global _probe_cache
    _probe_cache = None


def _searched() -> str:
    """A short, readable list of the places the engine was looked for."""
    seen: list[str] = []
    for path in candidate_paths()[:4]:
        text = str(path)
        if text not in seen:
            seen.append(text)
    return "; ".join(seen)
