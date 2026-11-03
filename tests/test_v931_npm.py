"""v9.3.2 npm launcher regression tests.

The launcher's download helper passed the artifact descriptor object to
``assetName(version, platform)``, which raised
``no prebuilt asset for undefined`` before any download was attempted. The
cache name, checksum lookup and URL now come from one ``artifactFileName()``
helper. These tests exercise the real Node modules; they are skipped only when
Node is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(script: str) -> str:
    proc = subprocess.run(
        [NODE, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_launcher_reports_the_package_version() -> None:
    proc = subprocess.run(
        [NODE, "npm/bin/seedcode.js", "--launcher-version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "seedcode-cli (npm launcher) v9.3.2"


def test_artifact_filename_url_and_checksum_name_agree() -> None:
    raw = _run(
        "const r = require('./npm/lib/resolve');"
        "const d = require('./npm/lib/download');"
        "const a = r.resolveArtifact('win32', 'x64');"
        "process.stdout.write(JSON.stringify({"
        "asset: a.asset,"
        "filename: d.artifactFileName(a),"
        "url: d.artifactUrl(a),"
        "checksums: d.checksumsUrl()"
        "}));"
    )
    data = json.loads(raw)
    expected = "SeedCode-CLI-9.3.2-windows-x64.exe"
    assert data["asset"] == expected
    assert data["filename"] == expected  # used for the cache + checksum lookup
    assert data["url"].endswith(f"/v9.3.2/{expected}")
    assert data["checksums"].endswith("/v9.3.2/SHA256SUMS.txt")


def test_packaged_manifest_lists_only_files_that_exist() -> None:
    package = json.loads((ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "9.3.2"
    assert package["bin"] == {"seedcode": "bin/seedcode.js"}
    for name in package["files"]:
        assert (ROOT / "npm" / name).is_file(), name
