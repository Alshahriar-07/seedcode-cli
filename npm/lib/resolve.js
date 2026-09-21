"use strict";

/**
 * Platform/architecture resolution for the Seed Code npm launcher.
 *
 * Maps Node's platform/arch identifiers onto the official release artifact
 * names. Windows ships a self-contained exe (downloaded to the user's cache
 * directory); Linux/macOS have no published prebuilt binary yet, so the
 * launcher points at the official installer instead of downloading
 * something that does not exist — it must never pretend an artifact exists
 * when it does not.
 */

// The official installation method (see IRM_INSTALL/). Quoted verbatim in
// error messages so the user is never sent to an unsupported path.
const OFFICIAL_INSTALL =
  "Install officially: curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash";

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
        `Windows builds are x64-only; this machine is ${arch}. ` + OFFICIAL_INSTALL
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
      `No prebuilt ${platform} binary is published yet. ` + OFFICIAL_INSTALL
    );
  }
  throw new Error(`Unsupported platform ${platform}. ` + OFFICIAL_INSTALL);
}

/** The Seed Code version a cached artifact belongs to (cache key). */
function artifactVersion(packageVersion) {
  return packageVersion;
}

module.exports = { resolveArtifact, artifactVersion, assetName, RELEASE_BASE };
