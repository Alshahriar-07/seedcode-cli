"""Stage the versioned release directory (build.bat stage 3).

Collects the final artifacts into ``dist/release/<version>/`` and writes a
real ``SHA256SUMS.txt`` — hashes are always computed from the staged files,
never invented. Missing optional artifacts (pip wheel/sdist, npm tarball)
are reported as absent; existing files are verified non-empty before
staging. Nothing here prints or logs any secret.

Output layout::

    dist/release/<version>/
    ├── SeedCode-CLI-<version>-windows-x64.exe   (standalone exe, renamed)
    ├── SeedCode-CLI-Setup-<version>.exe         (Inno Setup installer)
    ├── seedcode_cli-<version>-py3-none-any.whl  (when built)
    ├── seedcode_cli-<version>.tar.gz            (when built)
    └── SHA256SUMS.txt

Usage:
    python scripts/windows/stage_release.py --version <version> \
        --dist <repo>/dist --installer <repo>/Release/SeedCode-CLI-Setup-<version>.exe
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

_HEXDIGITS = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy(src: Path, dest_dir: Path, new_name: str | None = None) -> Path | None:
    if not src.is_file() or src.stat().st_size == 0:
        print(f"[MISS] {src.name} (not built)")
        return None
    target = dest_dir / (new_name or src.name)
    shutil.copy2(src, target)
    print(f"[OK] {target.name}  ({target.stat().st_size:,} bytes)")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release version (x.y.z)")
    parser.add_argument("--dist", required=True, help="repo dist/ directory")
    parser.add_argument(
        "--installer", required=True, help="path to the compiled Inno Setup installer"
    )
    opts = parser.parse_args()

    version = opts.version.strip()
    dist = Path(opts.dist).resolve()
    installer = Path(opts.installer).resolve()

    if not re.match(r"^\d+\.\d+\.\d+$", version):
        print(f"[ERROR] Unsupported version format: {version!r}")
        return 2
    if not installer.is_file() or installer.stat().st_size == 0:
        print(f"[ERROR] Installer missing or empty: {installer}")
        return 2

    release_dir = dist / "release" / version
    release_dir.mkdir(parents=True, exist_ok=True)

    staged: list[Path] = []

    # Standalone exe, renamed to the official release name.
    staged += [_copy(dist / "seedcode.exe", release_dir,
                     f"SeedCode-CLI-{version}-windows-x64.exe")]
    staged += [_copy(installer, release_dir)]

    # pip artifacts, when the wheel/sdist build ran first.
    # PEP 625: hatchling normalises the sdist name to `seedcode_cli-<version>.tar.gz`
    # (underscore), not `seedcode-cli-<version>.tar.gz` — match what is really built.
    for pattern in (f"seedcode_cli-{version}-*.whl", f"seedcode_cli-{version}.tar.gz"):
        matches = sorted(dist.glob(pattern))
        staged += [_copy(matches[-1], release_dir) if matches else None]

    # npm pack tarball, when the launcher package was packed.
    npm_tgz = dist / f"seedcode-cli-{version}.tgz"
    if npm_tgz.exists() and npm_tgz.stat().st_size > 0:
        staged += [_copy(npm_tgz, release_dir)]
    else:
        print("[MISS] npm tarball (not packed)")
        staged += [None]

    staged = [s for s in staged if s is not None]
    if not staged:
        print("[ERROR] No artifacts were staged - nothing to checksum.")
        return 2

    sums_path = release_dir / "SHA256SUMS.txt"
    lines = []
    for artifact in staged:
        digest = _sha256(artifact)
        if not _HEXDIGITS.match(digest):  # pragma: no cover - defensive
            print(f"[ERROR] Hash validation failed for {artifact.name}")
            return 2
        lines.append(f"{digest}  {artifact.name}")
    # newline="\n" is explicit: a checksum file staged on Windows must be
    # byte-identical to one written on Linux. Python's default newline
    # translation would write CRLF, and then `sha256sum -c` on macOS/Linux
    # (and install.sh's awk lookup) rejects every entry over the stray CR.
    with sums_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"[OK] {sums_path.name} ({len(lines)} entries, real hashes)")

    print("\nRelease directory ready:")
    print(f"  {release_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
