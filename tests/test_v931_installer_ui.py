"""v9.3.2 installer UI regression tests.

Both remote installers gained a monochrome banner, seven real installation
stages, a stage-accurate progress bar and actionable error codes. These tests
execute the real scripts/functions:

* the real ``install.sh`` end-to-end against a fake local "release" (fake
  ``uname``/``curl`` on PATH), including the checksum-failure path;
* the real ``show_banner`` function with redirected output;
* PowerShell 5.1 parsing of ``install.ps1`` plus rendering of its real
  ``Show-Banner``/``Show-Progress`` functions (skipped if PowerShell is absent).

macOS and PowerShell 7 are not available in this environment; that limitation
is reported in the release documentation rather than being claimed as tested.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SH_INSTALLER = ROOT / "IRM_INSTALL" / "install.sh"
PS_INSTALLER = ROOT / "IRM_INSTALL" / "install.ps1"
BASH = shutil.which("bash")
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")


def test_bash_installer_syntax() -> None:
    result = subprocess.run(
        [BASH, "-n", str(SH_INSTALLER)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_bash_installer_exposes_the_ui_contract() -> None:
    text = SH_INSTALLER.read_text(encoding="utf-8")
    for name in (
        "show_banner",
        "show_spinner",
        "write_status",
        "write_success",
        "write_warning",
        "write_error",
        "show_progress",
        "cleanup",
    ):
        assert f"\n{name}() {{" in "\n" + text, name
    assert "[%s] %s/%s" in text and "STAGE_TOTAL=7" in text


def test_bash_banner_renders_static_without_escapes() -> None:
    text = SH_INSTALLER.read_text(encoding="utf-8")
    start = text.index("show_banner() {")
    end = text.index("\n}\n", start) + 3
    source = text[start:end]
    harness = (
        "set -u\n"
        "UI_TTY=0; UI_WIDTH=80; UI_UNICODE=0; C_RESET=; C_WHITE=; C_GRAY=; C_DGRAY=\n"
        'SYM_DOT="|"; VERSION=9.3.2\n'
        f"{source}\n"
        "show_banner\n"
    )
    result = subprocess.run(
        [BASH, "-c", harness], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "SEED CODE CLI" in result.stdout
    assert "Version 9.3.2" in result.stdout
    assert "\x1b[" not in result.stdout


def _fake_release(tmp_path: Path, *, valid_checksum: bool = True) -> dict:
    """Build a fake release + PATH tools returning it for any release URL."""
    fakebin = tmp_path / "fakebin"
    bin_dir = tmp_path / "bin"
    install_dir = tmp_path / "install"
    for directory in (fakebin, bin_dir, install_dir):
        directory.mkdir(parents=True, exist_ok=True)

    binary = bin_dir / "seedcode"
    binary.write_text("#!/bin/sh\necho 'Seed Code CLI 9.3.2'\n", encoding="utf-8")
    binary.chmod(0o755)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if not valid_checksum:
        digest = "a" * 64

    (fakebin / "uname").write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in -s) echo Linux ;; -m) echo x86_64 ;; *) echo Linux ;; esac\n',
        encoding="utf-8",
    )
    (fakebin / "curl").write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            dest=""; args=("$@")
            for ((i=0; i<${#args[@]}; i++)); do
              [ "${args[$i]}" = "-o" ] && dest="${args[$((i+1))]}"
            done
            url="${args[${#args[@]}-1]}"
            case "$url" in
              *SHA256SUMS.txt)
                printf '%s  %s\\n' "$FAKE_HASH" "SeedCode-CLI-9.3.2-linux-x64" > "$dest" ;;
              *SeedCode-CLI-9.3.2-linux-x64)
                cp "$FAKE_BIN" "$dest" ;;
              *) exit 22 ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    for tool in ("uname", "curl"):
        (fakebin / tool).chmod(0o755)

    env = dict(os.environ)
    env.update(
        {
            "PATH": str(fakebin) + os.pathsep + env.get("PATH", ""),
            "FAKE_HASH": digest,
            "FAKE_BIN": str(binary),
            "NO_COLOR": "1",
        }
    )
    return {
        "env": env,
        "install_dir": install_dir,
        "checksum": digest,
    }


def _run_installer(fake: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            BASH,
            str(SH_INSTALLER),
            "--version",
            "9.3.2",
            "--bin-dir",
            fake["install_dir"].as_posix(),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        env=fake["env"],
    )


def test_bash_install_runs_all_seven_real_stages(tmp_path: Path) -> None:
    fake = _fake_release(tmp_path)
    result = _run_installer(fake)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    for stage in range(1, 8):
        assert f"{stage}/7" in result.stdout, f"stage {stage} missing"
    assert "Seed Code CLI installed successfully!" in result.stdout
    assert "\x1b[" not in output, "redirected output must not contain ANSI"
    installed = fake["install_dir"] / "seedcode"
    assert installed.is_file()
    version = subprocess.run(
        [BASH, str(installed), "--version"], capture_output=True, text=True, timeout=30
    )
    assert version.stdout.strip() == "Seed Code CLI 9.3.2"


def test_bash_checksum_failure_is_actionable(tmp_path: Path) -> None:
    fake = _fake_release(tmp_path, valid_checksum=False)
    result = _run_installer(fake)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "Installation failed" in output
    assert "Checksum mismatch" in output
    assert "Suggested action:" in output
    assert "Error code: CHECKSUM_MISMATCH" in output
    assert not (fake["install_dir"] / "seedcode").exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required")
def test_powershell_installer_parses_and_renders_the_banner(tmp_path: Path) -> None:
    script = tmp_path / "ps_ui_check.ps1"
    script.write_text(
        textwrap.dedent(
            r"""
            $parseErrors = $null; $tokens = $null
            $ast = [System.Management.Automation.Language.Parser]::ParseFile(
                $env:SEEDCODE_PS_INSTALLER, [ref]$tokens, [ref]$parseErrors)
            if ($parseErrors.Count) {
                $parseErrors | ForEach-Object { Write-Output $_.Message }
                exit 1
            }
            Write-Output 'PS_PARSE_OK'
            $names = @('Write-Ui', 'Show-Banner', 'Show-Progress', 'Show-Spinner',
                       'Write-Status', 'Write-Success', 'Write-Warning', 'Write-ErrorMessage')
            $src = ''
            foreach ($n in $names) {
                $fn = $ast.FindAll({
                    param($x)
                    $x -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -eq $n
                }, $true)[0]
                if ($null -eq $fn) { Write-Output "MISSING $n"; exit 1 }
                $src += $fn.Extent.Text + "`n"
            }
            $UiRedirected = $true; $UiAnsi = $false; $UiUnicode = $false; $Version = '9.3.2'
            $Sym = @{ Dot = '.'; Node = '*'; Tick = 'OK'; Cross = 'x'; Full = '#'; Empty = '.' }
            $AnsiFg = @{ White = '97'; Gray = '90'; Green = '92'; Yellow = '93'; Red = '91'; Cyan = '96'; DarkGray = '37' }
            Invoke-Expression $src
            Show-Banner
            Show-Progress -Current 4 -Total 7 -Label 'Downloading SeedCode CLI'
            """
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["SEEDCODE_PS_INSTALLER"] = str(PS_INSTALLER)
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "PS_PARSE_OK" in result.stdout
    assert "SEED CODE CLI" in result.stdout
    assert "Version 9.3.2" in result.stdout
    assert "4/7" in result.stdout
