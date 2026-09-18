"use strict";

/**
 * Download, verify, and cache the official Seed Code artifact.
 *
 * Security: the artifact's SHA256 is fetched from the release's
 * SHA256SUMS.txt and verified BEFORE the binary is executed. A mismatch
 * aborts loudly — never a silent pass. The cache lives in ~/.seedcode/npm
 * so repeated installs/offline starts work.
 */

const fs = require("fs");
const path = require("path");
const os = require("os");
const crypto = require("crypto");
const { RELEASE_BASE } = require("./resolve");
const { assetName } = require("./resolve");

const VERSION = require("../package.json").version;

function cacheRoot() {
  return path.join(os.homedir(), ".seedcode", "npm", VERSION);
}

function checksumsUrl() {
  return `${RELEASE_BASE}/v${VERSION}/SHA256SUMS.txt`;
}

function artifactUrl(artifact) {
  return `${RELEASE_BASE}/v${VERSION}/${assetName(artifact)}`;
}

function artifactUrl(artifact) {
  return `${RELEASE_BASE}/v${VERSION}/${assetName(VERSION, artifact.os === "windows" ? "win32" : artifact.os)}`;
}

function fetch(url, redirects = 5) {
  return new Promise((resolve, reject) => {
    const request = (url.startsWith("https:") ? require("https") : require("http")).get(
      url,
      (response) => {
        if (
          response.statusCode >= 300 &&
          response.statusCode < 400 &&
          response.headers.location &&
          redirects > 0
        ) {
          response.resume();
          resolve(fetch(response.headers.location, redirects - 1));
          return;
        }
        if (response.statusCode !== 200) {
          response.resume();
          reject(new Error(`HTTP ${response.statusCode} for ${url}`));
          return;
        }
        resolve(response);
      }
    );
    request.on("error", reject);
  });
}

async function downloadTo(url, destPath) {
  const response = await fetch(url);
  const tmp = `${destPath}.download`;
  await new Promise((resolve, reject) => {
    const file = fs.createWriteStream(tmp);
    response.pipe(file);
    file.on("finish", () => file.close(resolve));
    file.on("error", reject);
  });
  fs.renameSync(tmp, destPath);
}

function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    fs.createReadStream(filePath)
      .on("data", (chunk) => hash.update(chunk))
      .on("end", () => resolve(hash.digest("hex")))
      .on("error", reject);
  });
}

async function expectedChecksum(assetNameToCheck) {
  try {
    const response = await fetch(checksumsUrl());
    const body = await new Promise((resolve, reject) => {
      let data = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => (data += chunk));
      response.on("end", () => resolve(data));
      response.on("error", reject);
    });
    for (const line of body.split("\n")) {
      const match = line.trim().match(/^([0-9a-f]{64})\s+\*?(.+)$/);
      if (match && match[2].trim() === assetNameToCheck) {
        return match[1];
      }
    }
    return null; // asset not listed — treated as unverifiable below
  } catch (err) {
    return null; // checksums unavailable — treated as unverifiable below
  }
}

async function ensureDownloaded(artifact, packageVersion) {
  const root = cacheRoot();
  const target = path.join(root, assetName(artifact));

  if (fs.existsSync(target) && fs.statSync(target).size > 0) {
    return target;
  }
  fs.mkdirSync(root, { recursive: true });

  const url = artifactUrl(artifact);
  process.stderr.write(`Downloading Seed Code ${packageVersion} (${url})...\n`);
  await downloadTo(url, target);

  const actual = await sha256File(target);
  const expected = await expectedChecksum(assetName(artifact));
  if (!expected) {
    // No published checksum for this asset: refuse to cache an unverified
    // binary rather than silently trusting the download.
    fs.unlinkSync(target);
    throw new Error(
      "release checksum not found in SHA256SUMS.txt - refusing to run an unverified binary"
    );
  }
  if (actual !== expected) {
    fs.unlinkSync(target);
    throw new Error(
      `SHA256 mismatch (expected ${expected}, got ${actual}) - the download is corrupt or tampered with`
    );
  }
  return target;
}

module.exports = { ensureDownloaded, cacheRoot, checksumsUrl, artifactUrl };
