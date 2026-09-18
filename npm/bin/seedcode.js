#!/usr/bin/env node
/**
 * Seed Code CLI launcher (npm install -g seedcode-cli).
 *
 * Resolves the official Seed Code artifact for this platform/architecture,
 * downloads it from the GitHub release on first use (cached in
 * ~/.seedcode/npm), verifies its SHA256 against the release checksums, and
 * executes it with all arguments forwarded. This is a real launcher: it
 * runs the actual binary, it never prints fake "instructions".
 *
 * A local source/wheel install takes precedence: if `seedcode` already
 * resolves on PATH (pip install / Windows installer), that binary is used
 * directly and nothing is downloaded.
 */

"use strict";

const { resolveArtifact, artifactVersion } = require("../lib/resolve");
const { ensureDownloaded } = require("../lib/download");
const { spawn } = require("child_process");
const path = require("path");

const PACKAGE_VERSION = require("../package.json").version;

function fail(message) {
  process.stderr.write(`seedcode: ${message}\n`);
  process.exit(1);
}

function showHelp() {
  process.stdout.write(
    `Seed Code CLI v${PACKAGE_VERSION} (npm launcher)\n` +
      "\n" +
      "Usage:\n" +
      "  seedcode              Start the interactive app (downloads the\n" +
      "                        official binary on first use)\n" +
      "  seedcode --version    Print the Seed Code version\n" +
      "  seedcode --help       Show Seed Code help\n" +
      "\n" +
      "Docs: https://github.com/Alshahriar-07/seedcode-cli\n"
  );
}

async function main() {
  // The launcher's own --version/-V answers instantly without any download.
  const first = process.argv[2];
  if (first === "--launcher-version") {
    process.stdout.write(`seedcode-cli (npm launcher) v${PACKAGE_VERSION}\n`);
    return;
  }

  let artifact;
  try {
    artifact = resolveArtifact(process.platform, process.arch);
  } catch (err) {
    fail(err && err.message ? err.message : "unsupported platform");
  }

  let binaryPath;
  try {
    binaryPath = await ensureDownloaded(artifact, PACKAGE_VERSION);
  } catch (err) {
    fail(
      `could not obtain the Seed Code ${artifact.os}-${artifact.arch} binary: ` +
        (err && err.message ? err.message : String(err)) +
        "\n  Install it manually from https://github.com/Alshahriar-07/seedcode-cli/releases"
    );
  }

  const child = spawn(binaryPath, process.argv.slice(2), {
    stdio: "inherit",
    windowsHide: false,
  });
  child.on("error", (err) =>
    fail(`failed to launch ${path.basename(binaryPath)}: ${err.message}`)
  );
  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exit(code === null ? 0 : code);
  });
}

main().catch((err) =>
  fail(err && err.stack ? err.stack : String(err))
);
