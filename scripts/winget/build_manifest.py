"""Generate the Microsoft WinGet manifests for Seed Code CLI.

Produces the three YAML files the Microsoft winget-pkgs repository requires
for a multi-file manifest (schema 1.9.0):

    winget/manifests/s/SeedCode/CLI/<version>/SeedCode.CLI.yaml
    winget/manifests/s/SeedCode/CLI/<version>/SeedCode.CLI.installer.yaml
    winget/manifests/s/SeedCode/CLI/<version>/SeedCode.CLI.locale.en-US.yaml

The version is read from ``seedcode/__init__.py`` (the single source of truth)
unless overridden with ``--version``. The installer SHA256 is mandatory — the
manifest must never be generated with an invented hash.

Usage:
    python scripts/winget/build_manifest.py --sha256 04A7C68F...AFF5F
    python scripts/winget/build_manifest.py --version 5.0.3 --sha256 <hash>
    python scripts/winget/build_manifest.py --sha256 <hash> --out-dir out/

The generated files are ready to be copied into a fork of
https://github.com/microsoft/winget-pkgs under ``manifests/s/SeedCode/CLI/``
and submitted as a pull request.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PACKAGE_IDENTIFIER = "SeedCode.CLI"
PUBLISHER = "Eagox Studio"
PACKAGE_NAME = "Seed Code CLI"
AUTHOR = "Al Shahriar Sowan"
PROJECT_URL = "https://github.com/Alshahriar-07/seedcode-cli"
INSTALLER_FILENAME = "seedcode-cli-setup.exe"
MANIFEST_VERSION = "1.9.0"

_HEX64 = re.compile(r"^[0-9A-Fa-f]{64}$")


def _read_version() -> str:
    """Read the app version from seedcode/__init__.py (single source of truth)."""
    init = (REPO_ROOT / "seedcode" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init)
    if not match:
        raise SystemExit("Could not find __version__ in seedcode/__init__.py")
    return match.group(1)


def _installer_url(version: str) -> str:
    return f"{PROJECT_URL}/releases/download/v{version}/{INSTALLER_FILENAME}"


def _version_manifest(version: str) -> str:
    return f"""\
# yaml-language-server: $schema=https://aka.ms/winget-manifest.version.1.9.0.schema.json
# Created by scripts/winget/build_manifest.py - do not edit by hand.
PackageIdentifier: {PACKAGE_IDENTIFIER}
PackageVersion: {version}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: {MANIFEST_VERSION}
"""


def _installer_manifest(version: str, sha256: str, arp_name: str, arp_publisher: str) -> str:
    return f"""\
# yaml-language-server: $schema=https://aka.ms/winget-manifest.installer.1.9.0.schema.json
# Created by scripts/winget/build_manifest.py - do not edit by hand.
PackageIdentifier: {PACKAGE_IDENTIFIER}
PackageVersion: {version}
InstallerType: inno
InstallModes:
  - interactive
  - silent
  - silentWithProgress
UpgradeBehavior: install
ElevationRequirement: elevatesSelf
Installers:
  - Architecture: x64
    Scope: machine
    InstallerLocale: en-US
    InstallerUrl: {_installer_url(version)}
    InstallerSha256: {sha256}
    InstallerSwitches:
      Silent: /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
      SilentWithProgress: /SILENT /SUPPRESSMSGBOXES /NORESTART
      Interactive: /NORESTART
      InstallLocation: /DIR=<INSTALLPATH>
      Log: /LOG=<LOGPATH>
    Commands:
      - seedcode
    # Matches the entry the Inno Setup installer writes to Add/Remove Programs,
    # so `winget list`/`winget upgrade` can detect the installed package.
    # DisplayVersion is omitted because it equals PackageVersion.
    AppsAndFeaturesEntries:
      - DisplayName: {arp_name}
        Publisher: {arp_publisher}
ManifestType: installer
ManifestVersion: {MANIFEST_VERSION}
"""


def _locale_manifest(version: str) -> str:
    return f"""\
# yaml-language-server: $schema=https://aka.ms/winget-manifest.defaultLocale.1.9.0.schema.json
# Created by scripts/winget/build_manifest.py - do not edit by hand.
PackageIdentifier: {PACKAGE_IDENTIFIER}
PackageVersion: {version}
PackageLocale: en-US
Publisher: {PUBLISHER}
PublisherUrl: {PROJECT_URL}
PublisherSupportUrl: {PROJECT_URL}/issues
Author: {AUTHOR}
PackageName: {PACKAGE_NAME}
PackageUrl: {PROJECT_URL}
License: MIT
LicenseUrl: {PROJECT_URL}/blob/main/LICENSE
Copyright: Copyright (c) {AUTHOR}
ShortDescription: Seed Code CLI - a premium terminal-based AI coding assistant. Plant ideas. Grow code.
Description: >-
  Seed Code CLI is a terminal-based AI coding assistant with five independent
  backends: OpenRouter (full catalogue with free/paid filtering), FreeModel
  Claude and FreeModel Codex (free models), AeroLink, and local Ollama.
  It features streaming responses with live markdown and syntax highlighting,
  a startup dashboard and menu, per-provider conversation history, an agent
  mode with permissioned local tooling (read/edit/search/run), and quiet
  diagnostics. Windows, Linux, and macOS supported; a one-click Windows
  installer (Inno Setup) with PATH integration is available.
Moniker: seedcode
Tags:
  - ai
  - cli
  - terminal
  - chat
  - assistant
  - coding-assistant
  - agent
  - seedcode
  - openrouter
  - freemodel
  - aerolink
  - ollama
  - claude
  - codex
ManifestType: defaultLocale
ManifestVersion: {MANIFEST_VERSION}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default="",
        help="package version (default: read from seedcode/__init__.py)",
    )
    parser.add_argument("--sha256", required=True, help="SHA256 of the installer (uppercase hex)")
    parser.add_argument(
        "--out-dir",
        default="",
        help="output directory (default: winget/manifests/s/SeedCode/CLI/<version>/), "
        "matching the winget-pkgs partition layout",
    )
    parser.add_argument(
        "--arp-name",
        default="Seed Code",
        help="DisplayName the installer writes to Add/Remove Programs "
        "(default: 'Seed Code', the Inno AppName)",
    )
    parser.add_argument(
        "--arp-publisher",
        default=PUBLISHER,
        help="Publisher the installer writes to Add/Remove Programs "
        "(default: 'Eagox Studio'; use the legacy installer's value for older "
        "release assets)",
    )
    opts = parser.parse_args()

    version = opts.version or _read_version()
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        raise SystemExit(f"Unsupported version format: {version!r} (expected x.y.z)")
    sha256 = opts.sha256.upper()
    if not _HEX64.match(sha256):
        raise SystemExit("InstallerSha256 must be exactly 64 uppercase hex characters")

    out_dir = Path(opts.out_dir) if opts.out_dir else (
        REPO_ROOT / "winget" / "manifests" / "s" / "SeedCode" / "CLI" / version
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    files = {
        f"{PACKAGE_IDENTIFIER}.yaml": _version_manifest(version),
        f"{PACKAGE_IDENTIFIER}.installer.yaml": _installer_manifest(
            version, sha256, opts.arp_name, opts.arp_publisher
        ),
        f"{PACKAGE_IDENTIFIER}.locale.en-US.yaml": _locale_manifest(version),
    }
    for name, content in files.items():
        (out_dir / name).write_text(content, encoding="utf-8")
        print(f"[OK] wrote {out_dir / name}")

    print(f"\nPackageIdentifier : {PACKAGE_IDENTIFIER}")
    print(f"PackageVersion    : {version}")
    print(f"InstallerUrl      : {_installer_url(version)}")
    print(f"InstallerSha256   : {sha256}")
    print("\nSubmit these three files to https://github.com/microsoft/winget-pkgs")
    print("under manifests/s/SeedCode/CLI/<version>/ and open a pull request.")


if __name__ == "__main__":
    sys.exit(main())