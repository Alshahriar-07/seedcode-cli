#!/usr/bin/env bash
# Seed Code CLI v6.2.5 - official Linux/macOS remote installer.
#
# Usage (exactly as documented for remote install):
#
#     curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash
#
# Installs Seed Code CLI for the CURRENT USER: no admin rights, no cloned
# repository. The official release artifact is downloaded from GitHub
# Releases and its SHA256 is verified before anything is installed. When a
# prebuilt binary for this platform is published it is used directly;
# otherwise the official Python wheel is installed with pip.
#
# Options (download-then-run form):
#
#     curl -fsSL https://seedcode-cli.vercel.app/install.sh -o install.sh
#     bash install.sh --version 6.2.5 --no-path-update
#
# This script never sees, stores, or prints an API key.

set -euo pipefail

VERSION="6.2.5"
NO_PATH_UPDATE=0
FORCE=0
REPO="Alshahriar-07/seedcode-cli"
BIN_NAME="seedcode"

usage() {
  cat <<EOF
Seed Code CLI installer

Usage: install.sh [options]

  --version <x.y.z>   Release version to install (default: ${VERSION})
  --no-path-update    Install without modifying PATH
  --force             Reinstall even when this version is already installed
  -h, --help          Show this help

Installs Seed Code CLI, verifies the downloaded artifact's SHA256, adds the
install directory to your PATH, and then runs: seedcode --version
EOF
}

log()  { printf '  %s\n' "$*"; }
step() { printf '  - %s\n' "$*"; }
ok()   { printf '  OK  %s\n' "$*"; }
warn() { printf '  !   %s\n' "$*"; }

die() {
  printf '\n  Installer error: %s\n\n' "$*" >&2
  printf '  Download manually from: https://github.com/%s/releases\n\n' "$REPO" >&2
  exit 1
}

# --- arguments ---------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || die "--version needs a value like 6.2.5"; VERSION="$2"; shift 2 ;;
    --no-path-update) NO_PATH_UPDATE=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

case "$VERSION" in
  [0-9]*.[0-9]*.[0-9]*) : ;;
  *) die "Unsupported version format: '$VERSION' (expected x.y.z)" ;;
esac

RELEASE_BASE="https://github.com/${REPO}/releases/download/v${VERSION}"
SUMS_URL="${RELEASE_BASE}/SHA256SUMS.txt"

# --- platform detection ------------------------------------------------------
case "$(uname -s 2>/dev/null || echo unknown)" in
  Linux)  OS="linux" ;;
  Darwin) OS="macos" ;;
  *) die "Unsupported operating system '$(uname -s 2>/dev/null)'. Seed Code CLI installs on Linux and macOS; on Windows use install.ps1." ;;
esac

case "$(uname -m 2>/dev/null || echo unknown)" in
  x86_64|amd64)  ARCH="x64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  armv7l)        ARCH="armv7" ;;
  *) die "Unsupported processor architecture '$(uname -m 2>/dev/null)'." ;;
esac

# --- download helpers (curl or wget) ----------------------------------------
if command -v curl >/dev/null 2>&1; then
  HAVE_CURL=1
elif command -v wget >/dev/null 2>&1; then
  HAVE_CURL=0
else
  die "Neither curl nor wget is available; install one and try again."
fi

fetch_text() {
  # Prints the URL body, or nothing on failure. Never fatal.
  if [ "$HAVE_CURL" = 1 ]; then
    curl -fsSL --connect-timeout 20 --max-time 120 "$1" 2>/dev/null || true
  else
    wget -qO- --timeout=120 "$1" 2>/dev/null || true
  fi
}

fetch_file() {
  if [ "$HAVE_CURL" = 1 ]; then
    curl -fsSL --connect-timeout 20 --max-time 600 -o "$2" "$1" \
      || die "Could not download $1"
  else
    wget -q -O "$2" --timeout=600 "$1" || die "Could not download $1"
  fi
  [ -s "$2" ] || die "Download produced an empty file: $1"
}

sha256_of() {
  # Prints the bare lowercase-able digest. `tr -d '\\'` removes the escape
  # marker GNU coreutils prepends when the FILE NAME itself needs escaping -
  # which happens on MSYS/Git Bash, where the temp path contains backslashes.
  # Without it, a perfectly valid download on Git Bash would look like a
  # mismatch ('\<hash>' != '<hash>') and be refused.
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}' | tr -d '\\'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}' | tr -d '\\'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print $NF}' | tr -d '\\'
  else
    return 1
  fi
}

sums_lookup() {
  # $1 = SHA256SUMS.txt contents, $2 = exact file name to find.
  # Prints the expected lowercase SHA256, or nothing when absent.
  #
  # Parsing is deliberately whitespace-agnostic: GNU sha256sum writes two
  # spaces, other tools emit tabs, and a checksum file staged on Windows can
  # arrive with CRLF line endings. Leading/trailing whitespace is trimmed,
  # the hash is validated as exactly 64 hex digits, and the '*' binary-mode
  # marker is stripped before the file name is compared exactly.
  printf '%s\n' "$1" | awk -v want="$2" '
    {
      line = $0
      sub(/\r$/, "", line)
      sub(/^[ \t]+/, "", line)
      sub(/[ \t]+$/, "", line)
      if (line == "") next
      if (substr(line, 1, 1) == "#") next
      hash = substr(line, 1, 64)
      if (length(hash) != 64) next
      if (hash !~ /^[0-9A-Fa-f]+$/) next
      name = substr(line, 65)
      sub(/^[ \t]+/, "", name)
      sub(/^\*/, "", name)
      sub(/[ \t]+$/, "", name)
      if (name == want) { print tolower(hash); exit }
    }'
}

verify_sha256() {
  # $1 = file, $2 = sums text, $3 = file name (as listed in SHA256SUMS.txt)
  expected="$(sums_lookup "$2" "$3")"
  [ -n "$expected" ] \
    || die "No SHA256 checksum for $3 in the release's SHA256SUMS.txt. Refusing to install an unverified artifact."

  # A missing hashing tool is NOT an excuse to install unverified code: the
  # artifact is refused instead of silently accepted (never bypass verification).
  actual="$(sha256_of "$1")" \
    || die "No SHA256 tool found (sha256sum/shasum/openssl). Install one and re-run; refusing to install an unverified artifact."
  actual="$(printf '%s' "$actual" | tr 'A-F' 'a-f')"
  [ "$actual" = "$expected" ] \
    || die "Checksum mismatch for $3
      expected $expected
      actual   $actual"
  ok "SHA256 verified ($(printf '%s' "$actual" | cut -c1-12)...)"
}

# --- PATH --------------------------------------------------------------------
INSTALL_DIR=""
SHELL_PROFILE=""

choose_paths() {
  if [ -n "${HOME:-}" ] && [ -d "${HOME}/.local/bin" ]; then
    INSTALL_DIR="${HOME}/.local/bin"
  elif [ -n "${HOME:-}" ]; then
    INSTALL_DIR="${HOME}/.local/bin"
    mkdir -p "$INSTALL_DIR" 2>/dev/null || INSTALL_DIR="${HOME}/.seedcode/bin"
  else
    die "HOME is not set; cannot determine a per-user install location."
  fi

  case "$(basename "${SHELL:-}")" in
    zsh)  SHELL_PROFILE="${HOME}/.zshrc" ;;
    bash) SHELL_PROFILE="${HOME}/.bashrc" ;;
    *)    SHELL_PROFILE="${HOME}/.profile" ;;
  esac
}

add_to_path() {
  case ":${PATH}:" in
    *":${INSTALL_DIR}:"*) step "PATH already contains ${INSTALL_DIR}" ; return 0 ;;
  esac
  [ -n "$SHELL_PROFILE" ] || return 0
  if [ -f "$SHELL_PROFILE" ] && grep -qF "$INSTALL_DIR" "$SHELL_PROFILE" 2>/dev/null; then
    step "PATH entry already present in ${SHELL_PROFILE}"
    return 0
  fi
  {
    printf '\n# Seed Code CLI\n'
    printf 'export PATH="%s:$PATH"\n' "$INSTALL_DIR"
  } >> "$SHELL_PROFILE"
  ok "Added ${INSTALL_DIR} to PATH in ${SHELL_PROFILE}"
}

installed_version_matches() {
  command -v "$BIN_NAME" >/dev/null 2>&1 || return 1
  "$BIN_NAME" --version 2>/dev/null | grep -q "$VERSION"
}

# --- main --------------------------------------------------------------------
printf '\n  Seed Code CLI installer\n'
printf '  Version %s  |  %s-%s  |  per-user install\n' "$VERSION" "$OS" "$ARCH"

choose_paths

if [ "$FORCE" = 0 ] && installed_version_matches; then
  ok "Seed Code CLI ${VERSION} is already installed ($(command -v "$BIN_NAME"))"
  printf '\n  Run:  seedcode\n\n'
  exit 0
fi

log "Downloading"
step "${RELEASE_BASE}"

SUMS="$(fetch_text "$SUMS_URL")"
[ -n "$SUMS" ] || die "Could not fetch SHA256SUMS.txt for v${VERSION}. Check the version and your connection."

TMP_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t seedcode)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Prefer a published prebuilt binary for this platform; fall back to the
# official Python wheel (pure Python, so architecture-independent). The asset
# is chosen by an exact checksum-file lookup, not a substring match.
BINARY_ASSET="SeedCode-CLI-${VERSION}-${OS}-${ARCH}"
if [ -n "$(sums_lookup "$SUMS" "$BINARY_ASSET")" ]; then
  step "Downloading prebuilt binary ${BINARY_ASSET}"
  fetch_file "${RELEASE_BASE}/${BINARY_ASSET}" "${TMP_DIR}/${BINARY_ASSET}"
  verify_sha256 "${TMP_DIR}/${BINARY_ASSET}" "$SUMS" "$BINARY_ASSET"
  log "Installing"
  mkdir -p "$INSTALL_DIR" 2>/dev/null || die "Could not create ${INSTALL_DIR}"
  install -m 0755 "${TMP_DIR}/${BINARY_ASSET}" "${INSTALL_DIR}/${BIN_NAME}" 2>/dev/null \
    || { cp "${TMP_DIR}/${BINARY_ASSET}" "${INSTALL_DIR}/${BIN_NAME}" && chmod 0755 "${INSTALL_DIR}/${BIN_NAME}"; }
  ok "Installed to ${INSTALL_DIR}/${BIN_NAME}"
else
  WHEEL="seedcode_cli-${VERSION}-py3-none-any.whl"
  step "No prebuilt binary published for ${OS}-${ARCH}; installing the official wheel ${WHEEL}"
  fetch_file "${RELEASE_BASE}/${WHEEL}" "${TMP_DIR}/${WHEEL}"
  verify_sha256 "${TMP_DIR}/${WHEEL}" "$SUMS" "$WHEEL"

  PYTHON=""
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
  [ -n "$PYTHON" ] || die "Python 3.12+ is required for this artifact, and no python3 was found on PATH."

  log "Installing"
  "$PYTHON" -m pip install --user --upgrade "${TMP_DIR}/${WHEEL}" \
    || die "pip install failed. Install Python 3.12+ (with pip) and try again."

  # The console script lands in pip's per-user scripts directory; use that
  # as the install directory so PATH is updated for the right location.
  USER_SCRIPTS="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_path("scripts", "posix_user") or "")' 2>/dev/null || true)"
  if [ -n "$USER_SCRIPTS" ] && [ -d "$USER_SCRIPTS" ]; then
    INSTALL_DIR="$USER_SCRIPTS"
  fi
  ok "Installed ${WHEEL}"
fi

if [ "$NO_PATH_UPDATE" = 0 ]; then
  add_to_path
fi

printf '\n'
log "Verifying"
if command -v "$BIN_NAME" >/dev/null 2>&1; then
  REPORTED="$("$BIN_NAME" --version 2>&1 | head -n1)"
  case "$REPORTED" in
    *"$VERSION"*) ok "seedcode --version  ->  ${REPORTED}" ;;
    *) die "The installed command reported '$REPORTED', which is not version ${VERSION}." ;;
  esac
else
  die "Installed, but '${BIN_NAME}' is not on PATH yet. Add ${INSTALL_DIR} to PATH, then run: seedcode --version"
fi

printf '\n  Seed Code CLI %s is installed.\n\n' "$VERSION"
case ":${PATH}:" in
  *":${INSTALL_DIR}:"*)
    printf '  Run:  seedcode\n\n'
    ;;
  *)
    printf '  Open a NEW terminal, then run:  seedcode\n'
    printf '  (or apply it now: export PATH="%s:$PATH")\n\n' "$INSTALL_DIR"
    ;;
esac
