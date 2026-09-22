"""Installer checksum-parsing tests (v7.1.0 Windows + Linux installers).

The installers verify the release artifact's SHA256 before anything reaches
PATH, so the parser is security-critical: a lookup that misses refuses a valid
download (the reported Windows bug), and a lookup that is too loose could
accept the wrong file. These tests execute the REAL parsers:

* ``IRM_INSTALL/install.sh``'s ``sums_lookup`` / ``verify_sha256`` run under
  ``bash`` + ``awk`` against real files;
* ``IRM_INSTALL/install.ps1``'s ``Get-ExpectedHash`` runs under PowerShell
  (skipped when PowerShell is unavailable, e.g. a bare Linux CI box).

Both are also checked statically for the properties that make the check
mandatory rather than optional.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SH_INSTALLER = ROOT / "IRM_INSTALL" / "install.sh"
PS_INSTALLER = ROOT / "IRM_INSTALL" / "install.ps1"

BASH = shutil.which("bash")
AWK = shutil.which("awk")
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell") or shutil.which(
    "powershell.exe"
)

H1 = "75b1e4869085f02a34424d88bf8649788b765c5519a6007ae6994951957b8e09"
H2 = "ee92ba58a95f560b1774db9aa92c6645b23498e734ba08ee3f29114c7cdc7c19"
EXE = "SeedCode-CLI-6.2.5-windows-x64.exe"
SETUP = "SeedCode-CLI-Setup-6.2.5.exe"

# The exact SHA256SUMS.txt the release publishes, plus adversarial variants.
CASES: list[dict] = [
    {
        "label": "two spaces (GNU sha256sum)",
        "sums": f"{H1}  {EXE}\n{H2}  {SETUP}\n",
        "file": EXE,
        "want": H1,
    },
    {"label": "one space", "sums": f"{H1} {EXE}\n", "file": EXE, "want": H1},
    {"label": "tab separator", "sums": f"{H1}\t{EXE}\n", "file": EXE, "want": H1},
    {
        "label": "surrounding whitespace",
        "sums": f"   {H1}    {EXE}   \n",
        "file": EXE,
        "want": H1,
    },
    {
        "label": "CRLF line endings",
        "sums": f"{H1}  {EXE}\r\n{H2}  {SETUP}\r\n",
        "file": EXE,
        "want": H1,
    },
    {
        "label": "CRLF, second entry",
        "sums": f"{H1}  {EXE}\r\n{H2}  {SETUP}\r\n",
        "file": SETUP,
        "want": H2,
    },
    {"label": "binary-mode marker", "sums": f"{H1} *{EXE}\n", "file": EXE, "want": H1},
    {
        "label": "uppercase hash is normalised",
        "sums": f"{H1.upper()}  {EXE}\n",
        "file": EXE,
        "want": H1,
    },
    {"label": "absent file", "sums": f"{H1}  {EXE}\n", "file": "other.exe", "want": ""},
    {
        "label": "suffix is not a match",
        "sums": f"{H1}  {EXE}.bak\n",
        "file": EXE,
        "want": "",
    },
    {
        "label": "prefix is not a match",
        "sums": f"{H1}  old-{EXE}\n",
        "file": EXE,
        "want": "",
    },
    {"label": "non-hex hash ignored", "sums": f"nothex  {EXE}\n", "file": EXE, "want": ""},
    {"label": "short hash ignored", "sums": f"abc123  {EXE}\n", "file": EXE, "want": ""},
    {"label": "empty checksum file", "sums": "", "file": EXE, "want": ""},
    {"label": "comment line ignored", "sums": f"# {EXE}\n", "file": EXE, "want": ""},
    {
        "label": "sha256sum -c style header ignored",
        "sums": f"# checksums\n{H1}  {EXE}\n",
        "file": EXE,
        "want": H1,
    },
]


# --- extractors --------------------------------------------------------------


def _bash_function(text: str, name: str) -> str:
    """The source of one top-level ``name() { ... }`` shell function."""
    start = re.search(rf"^{name}\(\)\s*\{{", text, re.MULTILINE)
    assert start is not None, f"function {name} not found in install.sh"
    end = text.index("\n}\n", start.start()) + 3
    return text[start.start() : end]


def _ps_function(text: str, name: str) -> str:
    start = text.index(f"function {name}(")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=120, **kwargs
    )


# --- the Python reference hasher (for the end-to-end comparison test) --------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


# --- bash parser (install.sh) ------------------------------------------------


@pytest.mark.skipif(not (BASH and AWK), reason="bash + awk are required")
def test_shell_checksum_lookup_parses_every_release_format(tmp_path: Path) -> None:
    text = SH_INSTALLER.read_text(encoding="utf-8")
    functions = _bash_function(text, "sums_lookup")

    for index, case in enumerate(CASES):
        (tmp_path / f"case_{index}_sums").write_text(case["sums"], encoding="utf-8", newline="")
        (tmp_path / f"case_{index}_want").write_text(case["file"], encoding="utf-8")
        (tmp_path / f"case_{index}_expected").write_text(case["want"], encoding="utf-8")
        (tmp_path / f"case_{index}_label").write_text(case["label"], encoding="utf-8")

    harness = f"""set -u
{functions}
DIR={json.dumps(str(tmp_path))}
fail=0
i=0
while [ -f "$DIR/case_${{i}}_sums" ]; do
  got="$(sums_lookup "$(cat "$DIR/case_${{i}}_sums")" "$(cat "$DIR/case_${{i}}_want")")"
  want="$(cat "$DIR/case_${{i}}_expected")"
  label="$(cat "$DIR/case_${{i}}_label")"
  if [ "$got" != "$want" ]; then
    printf 'FAIL %s: got [%s] want [%s]\\n' "$label" "$got" "$want"
    fail=1
  fi
  i=$((i+1))
done
exit $fail
"""
    script = tmp_path / "lookup_test.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")
    result = _run([BASH, str(script)])

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not (BASH and AWK), reason="bash + awk are required")
def test_shell_verify_accepts_correct_and_refuses_wrong(tmp_path: Path) -> None:
    """Full verify path: real file, real digest, real refusal on mismatch."""
    text = SH_INSTALLER.read_text(encoding="utf-8")
    functions = "\n".join(
        _bash_function(text, name)
        for name in ("log", "step", "ok", "warn", "die", "sha256_of", "sums_lookup", "verify_sha256")
    )

    artifact = tmp_path / EXE
    artifact.write_bytes(b"seed code cli artifact\n")
    real = _sha256(artifact)

    harness = f"""set -uo pipefail
REPO=test/repo
{functions}
DIR={json.dumps(str(tmp_path))}
sums="$(cat "$DIR/sums.txt")"
verify_sha256 "$DIR/{EXE}" "$sums" "{EXE}"
echo "VERIFY_COMPLETED"
"""
    script = tmp_path / "verify_test.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")

    # 1. Correct checksum -> verifies and continues.
    (tmp_path / "sums.txt").write_text(f"{real}  {EXE}\n", encoding="utf-8", newline="\n")
    good = _run([BASH, str(script)])
    assert good.returncode == 0, good.stderr
    assert "VERIFY_COMPLETED" in good.stdout
    assert "SHA256 verified" in good.stdout

    # 2. Wrong checksum -> refuses, non-zero, never claims verified.
    (tmp_path / "sums.txt").write_text(f"{H1}  {EXE}\n", encoding="utf-8", newline="\n")
    bad = _run([BASH, str(script)])
    assert bad.returncode != 0
    assert "Checksum mismatch" in (bad.stdout + bad.stderr)
    assert "VERIFY_COMPLETED" not in bad.stdout

    # 3. Missing entry -> refuses rather than installing unverified.
    (tmp_path / "sums.txt").write_text(f"{H1}  unrelated.exe\n", encoding="utf-8", newline="\n")
    missing = _run([BASH, str(script)])
    assert missing.returncode != 0
    assert "No SHA256 checksum" in (missing.stdout + missing.stderr)

    # 4. Tabs / CRLF / extra whitespace still verify the SAME file.
    (tmp_path / "sums.txt").write_bytes(f"\t{real.upper()}\t{EXE}\r\n".encode())
    messy = _run([BASH, str(script)])
    assert messy.returncode == 0, messy.stderr
    assert "SHA256 verified" in messy.stdout


# --- PowerShell parser (install.ps1) -----------------------------------------


@pytest.mark.skipif(not (BASH and AWK), reason="bash + awk are required")
def test_shell_digest_is_a_bare_hex_string(tmp_path: Path) -> None:
    """GNU coreutils escapes names with a '\\' prefix; the digest must not carry it.

    On MSYS/Git Bash every Windows-style temp path is escaped, so without
    stripping the marker a valid download would fail verification.
    """
    text = SH_INSTALLER.read_text(encoding="utf-8")
    functions = _bash_function(text, "sha256_of")
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"seed code")
    script = tmp_path / "digest_test.sh"
    script.write_text(
        functions + f'\nsha256_of {json.dumps(str(artifact))}\n',
        encoding="utf-8",
        newline="\n",
    )
    result = _run([BASH, str(script)])
    assert result.returncode == 0, result.stderr
    digest = result.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", digest), digest
    assert digest == _sha256(artifact)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_powershell_checksum_lookup_parses_every_release_format(tmp_path: Path) -> None:
    text = PS_INSTALLER.read_text(encoding="utf-8")
    pattern_line = next(
        line for line in text.splitlines() if line.startswith("$SumsEntryPattern")
    )
    functions = pattern_line + "\n" + _ps_function(text, "Get-ExpectedHash")

    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(CASES), encoding="utf-8")

    harness = f"""{functions}
$data = Get-Content -Raw -Path "{cases_path}" | ConvertFrom-Json
$script:fail = 0
foreach ($case in $data) {{
    $got = Get-ExpectedHash $case.sums $case.file
    if ("$got" -ne "$($case.want)") {{
        Write-Host "FAIL $($case.label): got [$got] want [$($case.want)]"
        $script:fail = 1
    }}
}}
exit $script:fail
"""
    script = tmp_path / "lookup_test.ps1"
    script.write_text(harness, encoding="utf-8")

    result = _run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_powershell_lookup_handles_octet_stream_bytes(tmp_path: Path) -> None:
    """GitHub serves release assets as octet-stream; PS 5.1 hands back byte[]."""
    text = PS_INSTALLER.read_text(encoding="utf-8")
    pattern_line = next(
        line for line in text.splitlines() if line.startswith("$SumsEntryPattern")
    )
    functions = pattern_line + "\n" + _ps_function(text, "Get-ExpectedHash")

    harness = f"""{functions}
$sums = "$('{H1}')  {EXE}`n"
$bytes = [Text.Encoding]::UTF8.GetBytes($sums)
$got = Get-ExpectedHash $bytes "{EXE}"
if ("$got" -ne "{H1}") {{ Write-Host "FAIL byte[]: got [$got]"; exit 1 }}
$bom = [Text.Encoding]::UTF8.GetBytes("# header`n$('{H2}')`t{SETUP}`r`n")
$got2 = Get-ExpectedHash $bom "{SETUP}"
if ("$got2" -ne "{H2}") {{ Write-Host "FAIL byte[] tab/CRLF: got [$got2]"; exit 1 }}
exit 0
"""
    script = tmp_path / "bytes_test.ps1"
    script.write_text(harness, encoding="utf-8")
    result = _run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- static guarantees -------------------------------------------------------


def test_linux_installer_never_skips_verification() -> None:
    text = SH_INSTALLER.read_text(encoding="utf-8")
    assert "skipping checksum verification" not in text.lower()
    # The refusal happens for a missing entry AND for a missing hashing tool.
    refusals = text.lower().count("refusing to install an unverified artifact")
    assert refusals >= 2, "both the missing-entry and missing-tool paths must refuse"
    assert "Checksum mismatch" in text


def test_windows_installer_never_skips_verification() -> None:
    text = PS_INSTALLER.read_text(encoding="utf-8")
    assert "Refusing to install an unverified binary" in text
    assert "Checksum mismatch" in text
    assert "Get-FileHash" in text
    # The comparison is mandatory: no branch installs before it.
    verify_at = text.index("Get-ExpectedHash $sums $AssetName")
    install_at = text.index("Copy-Item -LiteralPath $tempExe -Destination $TargetExe")
    assert verify_at < install_at


def test_installers_verify_the_file_they_installed_not_one_on_path() -> None:
    """An older `seedcode` earlier on PATH must never pass as this install.

    Both installers verify the freshly installed binary by absolute path (the
    "old executable still runs" failure mode this project hit before), and both
    name a different `seedcode` that resolves earlier on PATH instead of
    silently letting the stale copy win.
    """
    sh_text = SH_INSTALLER.read_text(encoding="utf-8")
    ps_text = PS_INSTALLER.read_text(encoding="utf-8")

    # Verified by absolute path, not through PATH.
    assert 'INSTALLED_EXE="${INSTALL_DIR}/${BIN_NAME}"' in sh_text
    assert '"$INSTALLED_EXE" --version' in sh_text
    assert "(& $TargetExe --version" in ps_text

    # ...and any copy that would shadow this one is reported explicitly.
    assert "is earlier on PATH" in sh_text
    assert "is earlier on PATH" in ps_text


def test_installers_download_and_verify_the_documented_artifacts() -> None:
    sh_text = SH_INSTALLER.read_text(encoding="utf-8")
    ps_text = PS_INSTALLER.read_text(encoding="utf-8")

    # Artifact names are the release contract shared with
    # scripts/windows/stage_release.py: they must not be renamed, because both
    # installers look them up by exact name in SHA256SUMS.txt.
    assert "SeedCode-CLI-${VERSION}-${OS}-${ARCH}" in sh_text
    assert "seedcode_cli-${VERSION}-py3-none-any.whl" in sh_text
    assert "SHA256SUMS.txt" in sh_text and "SHA256SUMS.txt" in ps_text
    assert "$AssetName   = \"SeedCode-CLI-$Version-windows-$Arch.exe\"" in ps_text

    # Order in the shell installer: fetch, then verify, then install.
    fetch = sh_text.index('fetch_file "${RELEASE_BASE}/${BINARY_ASSET}"')
    verify = sh_text.index('verify_sha256 "${TMP_DIR}/${BINARY_ASSET}"')
    install = sh_text.index('install -m 0755 "${TMP_DIR}/${BINARY_ASSET}"')
    assert fetch < verify < install


def test_shell_parser_is_whitespace_agnostic_by_construction() -> None:
    """Guards against a regression to `$2 == want` style field splitting."""
    text = SH_INSTALLER.read_text(encoding="utf-8")
    body = _bash_function(text, "sums_lookup")
    assert "sub(/^[ \\t]+/" in body  # leading whitespace trimmed
    assert "length(hash) != 64" in body  # exactly 64 hex digits
    assert "name == want" in body  # exact filename comparison


# --- release/version consistency ---------------------------------------------


def test_installer_versions_are_synchronised_with_this_release() -> None:
    """`seedcode.__version__` is the canonical version; the installers carry a
    matching release constant so the two can never drift apart."""
    from seedcode import __version__

    sh_text = SH_INSTALLER.read_text(encoding="utf-8")
    ps_text = PS_INSTALLER.read_text(encoding="utf-8")
    info_text = (ROOT / "IRM_INSTALL" / "RELEASE_INFO.txt").read_text(encoding="utf-8")

    assert f'VERSION="{__version__}"' in sh_text
    assert f'[string] $Version = "{__version__}"' in ps_text
    assert f"v{__version__}" in info_text


def test_release_info_uses_the_canonical_version_everywhere() -> None:
    """The version literal must not appear in two different forms."""
    from seedcode import __version__

    major, minor, patch = __version__.split(".")
    stale = [f"{major}.{minor}.{n}" for n in range(int(patch))]

    sh_text = SH_INSTALLER.read_text(encoding="utf-8")
    ps_text = PS_INSTALLER.read_text(encoding="utf-8")
    for old in stale:
        # A hardcoded older release must not be selectable or documented.
        assert f'VERSION="{old}"' not in sh_text, old
        assert f'[string] $Version = "{old}"' not in ps_text, old
        # ...and the default-release doc examples must show the current one.
        assert f"--version {old}" not in sh_text, old
        assert f"-Version {old}" not in ps_text, old


def test_assets_carry_the_current_release_version() -> None:
    from seedcode import __version__

    sh_text = SH_INSTALLER.read_text(encoding="utf-8")
    ps_text = PS_INSTALLER.read_text(encoding="utf-8")
    assert "SeedCode-CLI-${VERSION}-${OS}-${ARCH}" in sh_text
    assert "SeedCode-CLI-$Version-windows-$Arch.exe" in ps_text
    # The published Windows names documented in the README match this version.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"SeedCode-CLI-{__version__}-windows-x64.exe" in readme
    assert f"SeedCode-CLI-Setup-{__version__}.exe" in readme
    assert "SHA256SUMS.txt" in readme


def test_official_install_commands_are_the_only_ones_documented() -> None:
    """npm / pip / winget are not current distribution channels."""
    banned = (
        "npm install -g seedcode-cli",
        "pip install seedcode-cli",
        "winget install SeedCode.CLI",
    )
    for path in (
        ROOT / "README.md",
        ROOT / "RELEASE.md",
        ROOT / "IRM_INSTALL" / "RELEASE_INFO.txt",
    ):
        text = path.read_text(encoding="utf-8")
        for pattern in banned:
            assert pattern not in text, (path.name, pattern)

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "irm https://seedcode-cli.vercel.app/install.ps1 | iex" in readme
    assert "curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash" in readme
