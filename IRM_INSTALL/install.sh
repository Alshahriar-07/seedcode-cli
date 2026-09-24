#!/usr/bin/env bash
# Seed Code CLI v8.1.0 - official remote installer (Linux / macOS / WSL).
#
# Installs Seed Code CLI for the CURRENT USER. No administrator rights and no
# cloned repository are required: the official release artifact is downloaded
# from GitHub Releases, its SHA256 is verified against the release's
# SHA256SUMS.txt, and `seedcode` is installed into your user environment.
#
# The installer prefers the platform standalone binary when the release
# publishes one for this OS/architecture (SeedCode-CLI-<ver>-<os>-<arch>) and
# otherwise falls back to the official wheel, installed with `pipx` when
# available (falling back to `pip install --user`). Whatever path is taken, the
# downloaded file is verified before anything is executed or installed.
#
# Usage (exactly as documented for remote install):
#
#   curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash
#
# Or, to pass options, download-then-run:
#
#   bash install.sh --version 8.1.0
#   bash install.sh --bin-dir ~/.local/bin
#
# Options:
#   --version V   Release version to install   (default: 8.1.0)
#   --bin-dir D   Where the seedcode binary should live (default: ~/.local/bin)
#   --force       Reinstall even if this version is already present
#   --plain       Disable the ANSI/Unicode UI
#   -h, --help    Show this help
#
# This script never sees, stores, or prints an API key; credentials are set
# up inside the app on first run.

set -u

# --- shell environment hardening ---------------------------------------------
IFS=$'\n\t'
umask 022

# --- constants ---------------------------------------------------------------
REPO="Alshahriar-07/seedcode-cli"
DEFAULT_VERSION="8.1.0"
USER_AGENT="seedcode-cli-installer/${DEFAULT_VERSION}"

# The release artifact names are the release contract shared with
# scripts/windows/stage_release.py and .github/workflows/release.yml: they are
# looked up by EXACT name in SHA256SUMS.txt, so they must never be renamed.
#   native binary : SeedCode-CLI-<version>-<os>-<arch>   (linux/darwin, x64/arm64)
#   pip wheel     : seedcode_cli-<version>-py3-none-any.whl
BIN_NAME="seedcode"

# --- options -----------------------------------------------------------------
VERSION="$DEFAULT_VERSION"
BIN_DIR="${HOME}/.local/bin"
FORCE=0
PLAIN="${NO_COLOR:+1}"
INSTALL_DIR=""

print_help() {
    sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version) [ $# -ge 2 ] || { echo "error: --version needs a value" >&2; exit 2; }; VERSION="$2"; shift 2 ;;
        --bin-dir) [ $# -ge 2 ] || { echo "error: --bin-dir needs a value" >&2; exit 2; }; BIN_DIR="$2"; shift 2 ;;
        --force)   FORCE=1; shift ;;
        --plain)   PLAIN=1; shift ;;
        -h|--help) print_help ;;
        *) echo "error: unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

INSTALL_DIR="$BIN_DIR"

# --- UI (ANSI / Unicode with graceful fallback) -------------------------------
# Every output helper is self-contained: it depends on no other function, so it
# is safe to call from the installer AND from the extracted test harnesses.
if [ -t 1 ] && [ -z "${PLAIN:-}" ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
    UI_TTY=1
else
    UI_TTY=0
fi

# Unicode is best-effort; everything degrades to ASCII cleanly.
if command -v locale >/dev/null 2>&1 && locale -k charmap 2>/dev/null | grep -qi 'UTF-8'; then
    UI_UNICODE=1
else
    UI_UNICODE=0
fi

if [ "$UI_UNICODE" = "1" ]; then
    SYM_RULE="─"; SYM_TICK="✓"; SYM_NODE="●"; SYM_DOT="•"; SYM_BRANCH="├"
else
    SYM_RULE="-"; SYM_TICK="OK"; SYM_NODE="*"; SYM_DOT="|"; SYM_BRANCH="|"
fi
RULE_TEXT=$(awk -v s="$SYM_RULE" 'BEGIN{for(i=0;i<38;i++) printf "%s", s}')

if [ "$UI_TTY" = "1" ]; then
    C_RESET=$'\033[0m'; C_GRAY=$'\033[90m'; C_GREEN=$'\033[92m'
    C_YELLOW=$'\033[93m'; C_RED=$'\033[91m'; C_CYAN=$'\033[96m'; C_DGRAY=$'\033[37m'
else
    C_RESET=""; C_GRAY=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""; C_DGRAY=""
fi

ui()      { printf '  %b\n' "$1"; }                    # raw line
title()   { printf '  %b%s%b\n' "${C_GREEN:-}" "$1" "${C_RESET:-}"; }
rule()    { printf '  %b%s%b\n' "${C_DGRAY:-}" "$RULE_TEXT" "${C_RESET:-}"; }
section() { printf '\n'; printf '  %b%s%b\n' "${C_CYAN:-}" "$1" "${C_RESET:-}"; }
ok()      { printf '  %b%s %s%b\n' "${C_GREEN:-}" "${SYM_TICK:-OK}" "$1" "${C_RESET:-}"; }
step()    { printf '  %b%s %s%b\n' "${C_GRAY:-}" "${SYM_BRANCH:-|}" "$1" "${C_RESET:-}"; }
log()     { printf '  %s\n' "$1"; }
# Warnings are advisory (never fatal); they go to stderr so they never pollute
# a value a caller captures from stdout.
warn()    { printf '  %b! %s%b\n' "${C_YELLOW:-}" "$1" "${C_RESET:-}" >&2; }

die() {  # clean, single, actionable error; non-zero exit
    printf '\n' >&2
    # %b (not %s) so multi-line messages keep their embedded newlines.
    printf '  %bInstaller error:%b %b\n\n' "${C_RED:-}" "${C_RESET:-}" "$1" >&2
    printf '  Download manually from: https://github.com/%s/releases\n\n' "${REPO:-Alshahriar-07/seedcode-cli}" >&2
    exit 1
}

# Ctrl+C must stop cleanly (no half-written state, correct exit code).
interrupted() {
    printf '\n' >&2
    printf '  %bInterrupted.%b Nothing was installed.\n' "${C_YELLOW:-}" "${C_RESET:-}" >&2
    exit 130
}
trap interrupted INT TERM

cleanup() {
    [ -n "${TMP_DIR:-}" ] && [ -d "${TMP_DIR}" ] && rm -rf "${TMP_DIR}" 2>/dev/null
    return 0
}
trap cleanup EXIT

# --- required tools -----------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required but was not found in PATH."; }

# --- checksum helpers ---------------------------------------------------------
# One SHA256SUMS.txt entry looks like:
#   <64 hex digits><spaces or tabs>[*]<file name>
# The separator is deliberately NOT assumed to be a single space: GNU
# sha256sum writes two spaces, other tools emit tabs, and a checksum file
# staged on Windows can arrive with CRLF line endings. '*' is the binary-mode
# marker some sha256sum builds write; it is not part of the file name.
#
# GNU awk (not sed/grep EREs) does the parsing: the lookup must be
# whitespace-agnostic AND strict about the digest length and the exact file
# name, which is awkward to express portably otherwise.

# sums_lookup <sums-text> <file-name>  -> prints the lowercase hash, or nothing
sums_lookup() {
    awk -v want="${2:-}" '
        {
            line = $0
            sub(/^[ \t]+/, "", line)
            if (line == "" || substr(line, 1, 1) == "#") next
            if (match(line, /^[0-9A-Fa-f]+/) == 0) next
            hash = substr(line, 1, RLENGTH)
            if (length(hash) != 64) next
            name = substr(line, RLENGTH + 1)
            sub(/^[ \t]+/, "", name)
            sub(/^\*/, "", name)
            sub(/[ \t\r]+$/, "", name)
            if (name == want) { print tolower(hash); exit }
        }
    ' <<< "${1:-}"
}

# sha256_of <file>  -> prints a bare 64-digit lowercase digest, or returns 1
sha256_of() {
    _sha=""
    if command -v sha256sum >/dev/null 2>&1; then
        _sha="$(sha256sum "$1" 2>/dev/null | awk 'NR==1 {print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        _sha="$(shasum -a 256 "$1" 2>/dev/null | awk 'NR==1 {print $1}')"
    elif command -v openssl >/dev/null 2>&1; then
        _sha="$(openssl dgst -sha256 "$1" 2>/dev/null | awk 'NR==1 {print $NF}')"
    else
        return 1
    fi
    # GNU coreutils prefixes a line it had to escape (a name containing a
    # backslash or newline) with '\'; drop every backslash so only the digest
    # remains, then normalise to lowercase.
    _sha="$(printf '%s' "$_sha" | tr -d '\\' | tr 'A-F' 'a-f')"
    [ "${#_sha}" -eq 64 ] || return 1
    case "$_sha" in
        *[!0-9a-f]*) return 1 ;;
    esac
    printf '%s' "$_sha"
}

# verify_sha256 <file> <sums-text> [<file-name>]
# Aborts (never returns) when the expected hash is missing or does not match.
verify_sha256() {
    _file="$1"
    _sums="${2:-}"
    _name="${3:-}"
    [ -n "$_name" ] || _name="$(basename "$_file")"
    _expected="$(sums_lookup "$_sums" "$_name")"
    if [ -z "$_expected" ]; then
        die "No SHA256 checksum for ${_name} in SHA256SUMS.txt - refusing to install an unverified artifact."
    fi
    if ! _actual="$(sha256_of "$_file")"; then
        die "No SHA-256 tool available (sha256sum/shasum/openssl) - refusing to install an unverified artifact."
    fi
    if [ "$_actual" != "$_expected" ]; then
        die "Checksum mismatch for ${_name}.
      expected ${_expected}
      actual   ${_actual}"
    fi
    ok "SHA256 verified  ${_actual}"
}

# --- fetch helper -------------------------------------------------------------
# fetch_file <url> <dest>  -> 0 on success, non-zero on any failure
fetch_file() {
    _url="$1"; _dest="$2"
    if command -v curl >/dev/null 2>&1; then
        if [ "${UI_TTY:-0}" = "1" ]; then
            # Real, byte-driven progress bar (never a fake animation).
            curl -fL --retry 2 --connect-timeout 15 -A "${USER_AGENT:-seedcode-cli-installer}" -# -o "$_dest" "$_url"
        else
            curl -fsSL --retry 2 --connect-timeout 15 -A "${USER_AGENT:-seedcode-cli-installer}" -o "$_dest" "$_url"
        fi
        return $?
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -q --tries=2 --timeout=30 -U "${USER_AGENT:-seedcode-cli-installer}" -O "$_dest" "$_url"
        return $?
    fi
    return 127
}

# --- artifact naming ----------------------------------------------------------
# ${OS} and ${ARCH} are the release's platform tokens (linux/darwin, x64/arm64).
OS=""
ARCH=""
BINARY_ASSET="SeedCode-CLI-${VERSION}-${OS}-${ARCH}"
WHEEL_ASSET="seedcode_cli-${VERSION}-py3-none-any.whl"

# --- main ---------------------------------------------------------------------
RELEASE_BASE="https://github.com/${REPO}/releases/download/v${VERSION}"
SUMS_URL="${RELEASE_BASE}/SHA256SUMS.txt"

printf '\n'
title "Seed Code CLI"
rule
ui "${C_GRAY:-}  Version ${VERSION}${C_RESET:-}"

section "${SYM_NODE} Checking system"
need uname
case "$(uname -s)" in
    Linux)  OS="linux" ;;
    Darwin) OS="darwin" ;;
    *)      die "Unsupported operating system '$(uname -s)'. Seed Code ships Linux and macOS builds." ;;
esac
case "$(uname -m)" in
    x86_64|amd64) ARCH="x64" ;;
    arm64|aarch64) ARCH="arm64" ;;
    *) die "Unsupported architecture '$(uname -m)'. Seed Code ships x64 and arm64 builds." ;;
esac
BINARY_ASSET="SeedCode-CLI-${VERSION}-${OS}-${ARCH}"
ok "Platform detected: ${OS} ${ARCH}"

if command -v curl >/dev/null 2>&1; then
    ok "Download tool detected: curl"
elif command -v wget >/dev/null 2>&1; then
    ok "Download tool detected: wget"
else
    die "Neither curl nor wget was found in PATH. Install one and re-run."
fi

TMP_DIR=$(mktemp -d 2>/dev/null) || die "Could not create a temporary directory."

section "${SYM_NODE} Downloading"
step "${BINARY_ASSET}"
if fetch_file "${RELEASE_BASE}/${BINARY_ASSET}" "${TMP_DIR}/${BINARY_ASSET}"; then
    TARGET_KIND="binary"
    TARGET_ASSET="${BINARY_ASSET}"
    ok "Standalone binary downloaded"
else
    # No native build for this platform in this release: use the wheel, which
    # is the portable artifact every release publishes.
    TARGET_KIND="wheel"
    TARGET_ASSET="${WHEEL_ASSET}"
    step "${WHEEL_ASSET}"
    fetch_file "${RELEASE_BASE}/${WHEEL_ASSET}" "${TMP_DIR}/${WHEEL_ASSET}" \
        || die "Could not download ${WHEEL_ASSET} from ${RELEASE_BASE}"
    ok "Package downloaded"
fi

section "${SYM_NODE} Verifying"
TMP_SUMS="${TMP_DIR}/SHA256SUMS.txt"
fetch_file "$SUMS_URL" "$TMP_SUMS" \
    || die "Could not download SHA256SUMS.txt from ${SUMS_URL} - refusing to install an unverified artifact."
SUMS_TEXT="$(cat "$TMP_SUMS")"
# verify_sha256 aborts on a missing entry or a mismatch; verification is never
# bypassed and there is no "skip" path. It is called on whichever artifact was
# downloaded, immediately before that artifact is installed.

section "${SYM_NODE} Installing"
INSTALLED_EXE="${INSTALL_DIR}/${BIN_NAME}"
mkdir -p "${INSTALL_DIR}" 2>/dev/null || true

if [ "${TARGET_KIND}" = "binary" ]; then
    # The exact, tested order: fetch -> verify -> install.
    verify_sha256 "${TMP_DIR}/${BINARY_ASSET}" "$SUMS_TEXT" "${BINARY_ASSET}"
    install -m 0755 "${TMP_DIR}/${BINARY_ASSET}" "${INSTALLED_EXE}" \
        || die "Could not install ${BINARY_ASSET} to ${INSTALLED_EXE}."
    ok "Seed Code CLI installed (standalone)"
else
    need python3
    PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) \
        || die "python3 exists but could not report its version."
    ok "Python detected: ${PY_VER}"
    if command -v pipx >/dev/null 2>&1; then
        if [ "$FORCE" = "1" ]; then
            pipx install --force "${TMP_DIR}/${WHEEL_ASSET}" >/dev/null \
                || die "pipx install failed. Re-run with output visible: pipx install ${TMP_DIR}/${WHEEL_ASSET}"
        else
            pipx install "${TMP_DIR}/${WHEEL_ASSET}" >/dev/null \
                || die "pipx install failed. Re-run with output visible: pipx install ${TMP_DIR}/${WHEEL_ASSET}"
        fi
        ok "Seed Code CLI installed (pipx)"
    else
        need pip3
        # PEP 668 (externally managed environments, Debian 12+/newer distros)
        # blocks plain `pip install --user`; --break-system-packages is the
        # sanctioned escape hatch and lands in the same user site.
        if ! pip3 install --user "${TMP_DIR}/${WHEEL_ASSET}" >/dev/null 2>&1; then
            pip3 install --user --break-system-packages "${TMP_DIR}/${WHEEL_ASSET}" >/dev/null 2>&1 \
                || die "pip install failed. Install pipx (python3 -m pip install --user pipx) and re-run this installer."
        fi
        ok "Seed Code CLI installed (pip --user)"
        if [ ! -e "${INSTALLED_EXE}" ]; then
            PYTHON_BIN="$(command -v python3)"
            printf '#!/usr/bin/env bash\nexec "%s" -m seedcode "$@"\n' "$PYTHON_BIN" > "${INSTALLED_EXE}"
            chmod +x "${INSTALLED_EXE}" 2>/dev/null || true
        fi
    fi
fi

section "${SYM_NODE} Verifying"
# Verify the file THIS run installed, by absolute path - a `seedcode` already
# on PATH could be an older copy, and a stale binary must never be reported as
# this install's success.
REPORTED="$("$INSTALLED_EXE" --version 2>/dev/null | tail -n1)"
case "$REPORTED" in
    *"$VERSION"*) ok "seedcode --version  ->  ${REPORTED}" ;;
    *) die "The installed binary did not report version ${VERSION} (it said: '${REPORTED:-nothing}')." ;;
esac

# A different `seedcode` earlier on PATH keeps being invoked instead of the one
# just installed (the "old executable still runs" failure mode). Say so out
# loud instead of letting the user believe the upgrade took effect.
ON_PATH="$(command -v seedcode 2>/dev/null || true)"
if [ -n "$ON_PATH" ] && [ "$ON_PATH" != "$INSTALLED_EXE" ] && [ "$ON_PATH" != "$(readlink -f "$INSTALLED_EXE" 2>/dev/null || printf '%s' "$INSTALLED_EXE")" ]; then
    printf '\n'
    printf '  %b! Another seedcode is earlier on PATH:%b\n' "${C_YELLOW:-}" "${C_RESET:-}"
    printf '  %b      %s%b\n' "${C_DGRAY:-}" "$ON_PATH" "${C_RESET:-}"
    printf '  %b      (this install: %s)%b\n' "${C_DGRAY:-}" "$INSTALLED_EXE" "${C_RESET:-}"
    printf '  %b    Remove the older copy, or run this one directly:%b\n' "${C_DGRAY:-}" "${C_RESET:-}"
    printf '  %b      %s --version%b\n' "${C_DGRAY:-}" "$INSTALLED_EXE" "${C_RESET:-}"
fi

# PATH guidance: honest about what actually works in THIS session.
case ":${PATH}:" in
    *":${INSTALL_DIR}:"*) PATH_OK=1 ;;
    *) PATH_OK=0 ;;
esac

printf '\n'
rule
printf '  %b%s Installation complete%b\n' "${C_GREEN:-}" "${SYM_TICK:-OK}" "${C_RESET:-}"
printf '\n'
if [ "$PATH_OK" = "1" ]; then
    ui "${C_GRAY:-}  Run:${C_RESET:-}"
    ui "${C_GRAY:-}      ${BIN_NAME}${C_RESET:-}"
else
    ui "${C_GRAY:-}  Restart your terminal (or: export PATH=\"${INSTALL_DIR}:\$PATH\"), then run:${C_RESET:-}"
    ui "${C_GRAY:-}      ${BIN_NAME}${C_RESET:-}"
fi
ui "${C_DGRAY:-}  License: PolyForm Noncommercial License 1.0.0${C_RESET:-}"
printf '\n'
