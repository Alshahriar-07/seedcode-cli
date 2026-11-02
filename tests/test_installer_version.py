"""Installer version-verification regression tests (v9.2.0).

All three installation verifiers must accept the CLI's real output —
``Seed Code CLI 9.2.0`` — while still rejecting anything that is not the
requested version. The one historical bug this locks down:

* ``setup.iss`` required a literal ``v`` in the output
  (``Pos('v{#MyAppVersion}', Reported) > 0``), so a correctly installed
  ``seedcode.exe`` reporting ``Seed Code CLI 9.2.0`` FAILED verification with
  "The installed seedcode.exe did not report version 9.2.0 (it said: 'Seed Code
  CLI 9.2.0')" — the exact report that shipped in the wild.

The three verifiers share one contract, implemented per language
(``version_ok`` in ``install.sh``, ``Test-InstalledVersion`` in
``install.ps1``, ``VersionInOutput`` in ``setup.iss``):

* the version must appear at a token boundary — never directly after a digit
  (``19.2.0`` does not pass for ``9.2.0``), never directly before a digit or a
  dot (``9.2.01`` / ``9.2.0.1`` do not pass for ``9.2.0``);
* a lone ``v``/``V`` version marker is accepted (``v9.2.0``); a marker inside a
  longer word is not (``rev9.2.0`` is refused);
* an empty or malformed output is always refused.

The bash and PowerShell matchers are executed here in full; the Inno Pascal
matcher cannot be executed outside Inno Setup, so it is statically asserted
and mirrored by the same boundary rules in this module.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SH_INSTALLER = ROOT / "IRM_INSTALL" / "install.sh"
PS_INSTALLER = ROOT / "IRM_INSTALL" / "install.ps1"
ISS_INSTALLER = ROOT / "scripts" / "windows" / "setup.iss"

BASH = shutil.which("bash")
AWK = shutil.which("awk")
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell") or shutil.which(
    "powershell.exe"
)

VERSION = "9.2.0"

# (reported output, should the expected version count as verified?)
BOUNDARY_CASES: list[tuple[str, bool]] = [
    ("Seed Code CLI 9.2.0", True),  # the CLI's actual --version output
    ("Seed Code CLI v9.2.0", True),  # v-marker variant
    ("v9.2.0", True),  # bare version with marker
    ("Seed Code CLI 9.2.0 (build 123)", True),  # trailing extra text
    ("9.2.0", True),  # bare version, no marker
    ("Seed Code CLI 19.2.0", False),  # longer build number
    ("Seed Code CLI 9.2.01", False),
    ("Seed Code CLI 9.2.0.1", False),
    ("SeedCodeCLI9.2.0", False),  # no boundary at all
    ("rev9.2.0", False),  # v-marker inside a longer word
    ("9.2.0RC1", True),  # pre-release suffix is still version 9.2.0
    ("", False),  # missing output
    ("(no output)", False),
    ("unrelated error text", False),
]


def version_in_output(text: str, want: str) -> bool:
    """Python mirror of the shared boundary contract (executable spec).

    Mirrors ``VersionInOutput`` (setup.iss) exactly: the Inno Pascal function
    cannot be run outside Inno Setup, so this pins its behaviour with the same
    rules and the same case matrix as the executed bash/PowerShell matchers.
    """
    n, m = len(text), len(want)
    if not want or n < m:
        return False
    for i in range(1, n - m + 2):
        if text[i - 1 : i - 1 + m] != want:
            continue
        before = text[i - 2] if i > 1 else " "
        if before.isdigit() or before == ".":
            continue
        if before.isalpha() and before not in ("v", "V"):
            continue
        if before in ("v", "V"):
            before2 = text[i - 3] if i > 2 else " "
            if before2.isalpha():
                continue
        after = text[i + m - 1] if i + m - 1 < n else " "
        if after.isdigit() or after == ".":
            continue
        return True
    return False


def _assert_matrix(matcher) -> None:
    """Every case decides identically; mismatches name the case."""
    bad = []
    for text, want in BOUNDARY_CASES:
        got = bool(matcher(text))
        if got != want:
            bad.append(f"{text!r}: got {got}, want {want}")
    assert not bad, "\n".join(bad)


def _bash_function(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _ps_function(text: str, name: str) -> str:
    start = text.index(f"function {name}(")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


# --- the real CLI output feeds every real verifier ---------------------------


def _cli_version_output() -> str:
    """``python -m seedcode --version`` output — what installers match against."""
    proc = subprocess.run(
        [sys.executable, "-m", "seedcode", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_real_cli_output_passes_bash_version_matcher() -> None:
    reported = _cli_version_output()
    assert version_ok_bash(reported, VERSION), f"bash matcher refused: {reported!r}"


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required")
@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required")
def test_real_cli_output_passes_powershell_version_matcher(tmp_path: Path) -> None:
    reported = _cli_version_output()
    exe = _stub_exe(tmp_path, reported)
    assert _test_installed_version_ps(
        exe,
        _ps_function(PS_INSTALLER.read_text(encoding="utf-8"), "Test-InstalledVersion"),
    ), f"PowerShell matcher refused: {reported!r}"


def test_real_cli_output_passes_version_boundary_contract() -> None:
    reported = _cli_version_output()
    assert version_in_output(reported, VERSION), f"contract refused: {reported!r}"


def test_boundary_contract_matrix() -> None:
    """The executable spec (setup.iss mirror) over the shared case matrix."""
    _assert_matrix(lambda text: version_in_output(text, VERSION))


# --- the bash matcher (install.sh: version_ok) --------------------------------


@pytest.mark.skipif(not (BASH and AWK), reason="bash + awk are required")
def test_bash_version_matcher_matrix(tmp_path: Path) -> None:
    """The REAL ``version_ok`` from install.sh over the shared matrix."""
    source = _bash_function(SH_INSTALLER.read_text(encoding="utf-8"), "version_ok")
    case_files: list[Path] = []
    body = ["set -u", source, "fail=0"]
    for index, (text, want) in enumerate(BOUNDARY_CASES):
        case = tmp_path / f"case_{index}.txt"
        case.write_text(text, encoding="utf-8", newline="\n")
        case_files.append(case)
        body.append(
            f'if version_ok "$(cat "{case.as_posix()}")" "{VERSION}"; '
            f"then got=YES; else got=NO; fi"
        )
        marker = "YES" if want else "NO"
        body.append(
            f'if [ "$got" != "{marker}" ]; then echo "FAIL case {index}: '
            f'$got, want {marker}"; fail=1; fi'
        )
    body.append("exit $fail")
    script = tmp_path / "version_matrix.sh"
    script.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
    result = subprocess.run(
        [BASH, str(script)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr


def version_ok_bash(reported: str, want: str) -> bool:
    """Run the REAL ``version_ok`` from install.sh on ``reported``."""
    import tempfile

    source = _bash_function(SH_INSTALLER.read_text(encoding="utf-8"), "version_ok")
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "reported.txt"
        case.write_text(reported, encoding="utf-8", newline="\n")
        script = Path(tmp) / "version_ok_test.sh"
        script.write_text(
            f"set -u\n{source}\n"
            f'if version_ok "$(cat "{case.as_posix()}")" "{want}"; then exit 0; fi\n'
            "exit 1\n",
            encoding="utf-8",
            newline="\n",
        )
        result = subprocess.run([BASH, str(script)], capture_output=True, timeout=60)
        return result.returncode == 0


# --- the PowerShell matcher (install.ps1: Test-InstalledVersion) ---------------


def _stub_exe(directory: Path, reported: str) -> Path:
    """A fake installed executable whose ``--version`` prints ``reported``.

    On Windows the stub is a ``.cmd`` whose output comes from ``type`` on a
    text file, so parens, quotes and other shell-significant characters in the
    reported string survive verbatim. On other hosts (pwsh on CI) it is a
    small POSIX shell script.
    """
    directory.mkdir(parents=True, exist_ok=True)
    text_file = directory / "reported.txt"
    text_file.write_text(reported, encoding="utf-8", newline="\n")
    exe = directory / "fake-seedcode.cmd"
    if sys.platform == "win32":
        exe.write_text(
            f'@echo off\ntype "{text_file}"\n', encoding="utf-8", newline="\n"
        )
    else:
        exe.write_text(
            f"#!/bin/sh\ncat '{text_file.as_posix()}'\n",
            encoding="utf-8",
            newline="\n",
        )
        exe.chmod(0o755)
    return exe


def _test_installed_version_ps(exe: Path, function: str) -> bool:
    """Execute the extracted ``Test-InstalledVersion`` function with ``exe``."""
    script = "\n".join(
        [
            function,
            f"if (Test-InstalledVersion '{exe.as_posix()}' '{VERSION}') {{",
            "  Write-Output 'VERSION_OK'",
            "}",
        ]
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return "VERSION_OK" in result.stdout


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required")
def test_powershell_version_matcher_matrix(tmp_path: Path) -> None:
    """The REAL ``Test-InstalledVersion`` over the shared matrix."""
    function = _ps_function(
        PS_INSTALLER.read_text(encoding="utf-8"), "Test-InstalledVersion"
    )
    for index, (text, want) in enumerate(BOUNDARY_CASES):
        exe = _stub_exe(tmp_path / f"case_{index}", text)
        got = _test_installed_version_ps(exe, function)
        assert got == want, f"case {index} {text!r}: got {got}, want {want}"


# --- the Inno Pascal matcher (setup.iss: VersionInOutput) ----------------------


def test_setup_iss_version_matcher_contract() -> None:
    """The Inno verifier no longer requires a 'v' prefix in the output.

    Historically the verification was ``Pos('v{#MyAppVersion}', Reported) > 0``
    — the exact bug that rejected a correctly installed binary that reported
    ``Seed Code CLI 9.2.0``.
    """
    text = ISS_INSTALLER.read_text(encoding="utf-8")
    assert "Pos('v{#MyAppVersion}'" not in text, (
        "setup.iss still matches a literal 'v' prefix — the historical "
        "verification bug is back"
    )
    assert "VersionInOutput(Reported, '{#MyAppVersion}')" in text
    assert re.search(r"^function VersionInOutput\(const Reported, Version: string\): Boolean;", text, re.MULTILINE)
    # The failure message still names the problem in user terms.
    assert "did not report version" in text
