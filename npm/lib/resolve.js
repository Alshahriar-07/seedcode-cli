"use strict";

/**
 * Platform/architecture resolution for the Seed Code npm launcher.
 *
 * Maps Node's platform/arch identifiers onto the official release artifact
 * names. Windows ships a self-contained exe (downloaded to the user's cache
 * directory); Linux/macOS currently install through pip, so the launcher
 * explains the pip path instead of downloading something that does not
 * exist — it must never pretend an artifact exists when it does not.
 */

const VERSION = require("../package.json").version;

const RELEASE_BASE =
  "https://github.com/Alshahriar-07/seedcode-cli/releases/download";

function assetName(version, platform) {
  if (platform === "win32") {
    // The standalone exe carries its own Python runtime (not the installer).
    return `SeedCode-CLI-${version}-windows-x64.exe`;
  }
  throw new Error(`no prebuilt asset for ${platform}`);
}

function resolveArtifact(platform, arch) {
  if (platform === "win32") {
    if (arch !== "x64" && arch !== "arm64") {
      throw new Error(
        `Windows builds are x64-only; this machine is ${arch}. ` +
          "Use pip: python -m pip install seedcode-cli"
      );
    }
    return {
      os: "windows",
      arch: "x64",
      kind: "standalone-exe",
      asset: assetName(VERSION, platform),
      url: `${RELEASE_BASE}/v${VERSION}/${assetName(VERSION, platform)}`,
    };
  }
  if (platform === "linux" || platform === "darwin") {
    throw new Error(
      `No prebuilt ${platform} binary is published yet. ` +
        "Install with pip: python -m pip install seedcode-cli"
    );
  }
  throw new Error(
    `Unsupported platform ${platform}. ` +
      "Install with pip: python -m pip install seedcode-cli"
  );
}

/** The Seed Code version a cached artifact belongs to (cache key). */
function artifactVersion(packageVersion) {
  return packageVersion;
}

module.exports = { resolveArtifact, artifactVersion, assetName, RELEASE_BASE };
