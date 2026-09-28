"""v9.1.0 checksum workflow tests: ONE deterministic checksum source.

The reported installer bug was a stale pinned digest disagreeing with a freshly
rebuilt binary:

    "The release's SHA256SUMS.txt and this installer's pinned checksum disagree
     for SeedCode-CLI-<previous>-windows-x64.exe."

The fix is a single source of truth: ``SHA256SUMS.txt``, generated from the
FINAL artifacts by ``scripts/windows/stage_release.py`` (and regenerated over
the complete asset set by the release workflow). These tests execute that
generator for real and prove:

* the digest in ``SHA256SUMS.txt`` is the SHA256 of the exact file it names;
* EXE digest == ``SHA256SUMS.txt`` digest == what the installer looks up;
* rebuilding the artifact regenerates the digest (no stale value can survive);
* the Windows installer carries no second, hand-maintained checksum;
* the release workflow regenerates checksums after the last artifact is built.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STAGE_RELEASE = ROOT / "scripts" / "windows" / "stage_release.py"
PS_INSTALLER = ROOT / "IRM_INSTALL" / "install.ps1"
SH_INSTALLER = ROOT / "IRM_INSTALL" / "install.sh"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")


def _load_stage_release():
    spec = importlib.util.spec_from_file_location("stage_release", STAGE_RELEASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage(
    tmp_path: Path,
    version: str = "9.1.0",
    *,
    exe_bytes: bytes = b"MZ fake standalone executable v1\n",
    setup_bytes: bytes = b"MZ fake inno setup installer v1\n",
) -> Path:
    """Run the real release stager over fake-but-real artifacts."""
    dist = tmp_path / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "seedcode.exe").write_bytes(exe_bytes)
    installer = tmp_path / "Release" / f"SeedCode-CLI-Setup-{version}.exe"
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_bytes(setup_bytes)

    result = subprocess.run(
        [
            sys.executable,
            str(STAGE_RELEASE),
            "--version",
            version,
            "--dist",
            str(dist),
            "--installer",
            str(installer),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return dist / "release" / version


def test_generated_checksums_match_the_final_artifacts(tmp_path: Path) -> None:
    """EXE SHA256 == SHA256SUMS.txt SHA256 == the installer's expected SHA256."""
    version = "9.1.0"
    release = _stage(tmp_path, version)
    exe = release / f"SeedCode-CLI-{version}-windows-x64.exe"
    setup = release / f"SeedCode-CLI-Setup-{version}.exe"
    sums = release / "SHA256SUMS.txt"

    assert exe.is_file() and setup.is_file() and sums.is_file()

    entries = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, name = re.split(r"\s+", line.strip(), maxsplit=1)
        entries[name] = digest

    # The checksum file describes exactly the staged artifacts...
    assert set(entries) == {exe.name, setup.name}
    # ...and every digest is the digest of the bytes actually shipped.
    assert entries[exe.name] == _sha256(exe)
    assert entries[setup.name] == _sha256(setup)
    # The artifact the Windows installer downloads is covered by name.
    assert f"SeedCode-CLI-{version}-windows-x64.exe" in entries


def test_rebuilding_an_artifact_regenerates_its_checksum(tmp_path: Path) -> None:
    """A changed binary must not keep an old digest (the reported bug)."""
    version = "9.1.0"
    release = _stage(tmp_path, version)
    exe = release / f"SeedCode-CLI-{version}-windows-x64.exe"
    sums = release / "SHA256SUMS.txt"
    first = _sha256(exe)
    assert first in sums.read_text(encoding="utf-8")

    # Rebuild the binary exactly as the pipeline would, then re-stage. The
    # generator must re-hash what it wrote NOW, not reuse the previous run.
    _stage(
        tmp_path,
        version,
        exe_bytes=b"MZ fake standalone executable v2 (rebuilt)\n",
        setup_bytes=b"MZ fake inno setup installer v2 (rebuilt)\n",
    )

    second = _sha256(exe)
    assert second != first
    text = sums.read_text(encoding="utf-8")
    assert second in text
    assert first not in text, "stale checksum survived a rebuild"


def test_checksum_file_uses_lf_endings_and_two_space_separator(tmp_path: Path) -> None:
    """`sha256sum -c` and install.sh must both parse the same file."""
    release = _stage(tmp_path, "9.1.0")
    raw = (release / "SHA256SUMS.txt").read_bytes()
    assert b"\r" not in raw
    assert all(
        re.fullmatch(rb"[0-9a-f]{64}  .+", line)
        for line in raw.splitlines()
    )


# --- one source: no second, hand-maintained checksum --------------------------


def test_windows_installer_has_no_pinned_checksum() -> None:
    text = PS_INSTALLER.read_text(encoding="utf-8")
    # No second, hand-maintained checksum table...
    assert "PinnedChecksums" not in text
    # ...and no hardcoded digest literal anywhere: that literal is exactly the
    # second source of truth that drifted from the rebuilt binary.
    assert _HEX64.findall(text) == [], _HEX64.findall(text)
    assert "SHA256SUMS.txt" in text


def test_both_installers_read_only_the_generated_checksum_file() -> None:
    ps = PS_INSTALLER.read_text(encoding="utf-8")
    sh = SH_INSTALLER.read_text(encoding="utf-8")
    assert "SHA256SUMS.txt" in ps and "SHA256SUMS.txt" in sh
    # The expected value comes from that file, and the artifact is compared
    # against it with a real hash of the downloaded bytes.
    assert "Get-ExpectedHash $sums $AssetName" in ps
    assert "Get-FileHash" in ps
    assert "sums_lookup" in sh


def test_release_workflow_generates_checksums_after_the_last_artifact() -> None:
    """Checksums must be produced AFTER every artifact exists, and verified."""
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assemble = text.index("Assemble the release asset directory")
    regenerate = text.index(": > SHA256SUMS.txt")
    verify = text.index("Verify the checksums describe the final artifacts")
    assert assemble < regenerate < verify
    # The staged Windows checksums are verified against the binaries too.
    assert "Assert-Checksum" in text


def test_stage_release_hashes_the_files_it_just_wrote() -> None:
    """Guards against a regression to reading a cached/old digest."""
    module = _load_stage_release()
    assert module._sha256.__module__ == module.__name__
    source = STAGE_RELEASE.read_text(encoding="utf-8")
    assert "_sha256(artifact)" in source
    assert 'newline="\\n"' in source


#: The version this release replaces. Assembled from parts on purpose so a
#: future blind find-and-replace of the current version cannot silently invert
#: this assertion (which is exactly how it was caught here).
_PREVIOUS_RELEASE = ".".join(("8", "2", "5"))


@pytest.mark.parametrize("path", [PS_INSTALLER, SH_INSTALLER, RELEASE_WORKFLOW])
def test_no_stale_reference_release_tags_remain(path: Path) -> None:
    """No installer/workflow may still point at the previous release."""
    text = path.read_text(encoding="utf-8")
    assert _PREVIOUS_RELEASE not in text
