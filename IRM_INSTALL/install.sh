#!/usr/bin/env bash
# Seed Code CLI v7.2.5 - official remote installer (Linux / macOS / WSL).
#
# Installs Seed Code CLI for the CURRENT USER. No administrator rights, no
# virtualenv, and no cloned repository are required: the official release
# wheel is downloaded from GitHub Releases, its SHA256 is verified, and
# `seedcode` is installed into your user environment with `pipx` when
# available (falling back to `pip install --user`).
#
# Usage (exactly as documented for remote install):
#
#   curl -fsSL https://seedcode-cli.vercel.app/install.sh | bash
#
# Or, to pass options, download-then-run:
#
#   bash install.sh --version 7.2.5
#   bash install.sh --bin-dir ~/.local/bin
#
# Options:
#   --version V   Release version to install   (default: 7.2.5)
#   --bin-dir D   Where the seedcode shim should live (default: ~/.local/bin)
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
DEFAULT_VERSION="7.2.5"
USER_AGENT="seedcode-cli-installer/${DEFAULT_VERSION}"

# Pinned SHA256 digests for the official v7.2.5 release artifacts. Each value
# was verified against the artifact actually published to GitHub Releases.
# They are a cross-check against, and a fallback for, SHA256SUMS.txt - never
# a substitute that bypasses verification.
PINNED_WHEEL="1ce44036d45a99630e557f44ee1afd2f45c2be7dc09d475dfd151c392d1d8bec"

# --- options -----------------------------------------------------------------
VERSION="$DEFAULT_VERSION"
BIN_DIR="${HOME}/.local/bin"
FORCE=0
PLAIN="${NO_COLOR:+1}"

print_help() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version) [ $# -ge 2 ] || { echo "error: --version needs a value" >&2; exit 2; }; VERSION="$2"; shift 2 ;;
        --bin-dir) [ $# -ge 2 ] || { echo "error: --bin-dir needs a value" ; exit 2; }; BIN_DIR="$2"; shift 2 ;;
        --force)   FORCE=1; shift ;;
        --plain)   PLAIN=1; shift ;;
        -h|--help) print_help ;;
        *) echo "error: unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

# --- UI (ANSI / Unicode with graceful fallback) -------------------------------
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
    SYM_RULE="─"; SYM_TICK="✓"; SYM_NODE="●"; SYM_DOT="•"
else
    SYM_RULE="-"; SYM_TICK="OK"; SYM_NODE="*"; SYM_DOT="|"
fi
RULE_TEXT=$(awk -v s="$SYM_RULE" 'BEGIN{for(i=0;i<38;i++) printf "%s", s}')

if [ "$UI_TTY" = "1" ]; then
    C_RESET=$'\033[0m'; C_GRAY=$'\033[90m'; C_GREEN=$'\033[92m'
    C_YELLOW=$'\033[93m'; C_RED=$'\033[91m'; C_CYAN=$'\033[96m'; C_DGRAY=$'\033[37m'
else
    C_RESET=""; C_GRAY=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""; C_DGRAY=""
fi

ui()      { printf '  %b\n' "$1"; }                      # raw line
title()   { ui "${C_GREEN}$1${C_RESET}"; }
rule()    { ui "${C_DGRAY}  $RULE_TEXT${C_RESET}"; }
section() { printf '\n'; ui "${C_CYAN}  $1${C_RESET}"; }
ok()      { ui "${C_GREEN}  ${SYM_TICK} $1${C_RESET}"; }
step()    { ui "${C_GRAY}  $1${C_RESET}"; }
note()    { ui "${C_DGRAY}  $1${C_RESET}"; }

die() {  # clean, single, actionable error; non-zero exit
    printf '\n' >&2
    # %b (not %s) so multi-line messages keep their embedded newlines.
    printf '  %bInstaller error:%b %b\n\n' "${C_RED}" "${C_RESET}" "$1" >&2
    printf '  Download manually from: https://github.com/%s/releases\n\n' "$REPO" >&2
    exit 1
}

# Ctrl+C must stop cleanly (no half-written state, correct exit code).
interrupted() {
    printf '\n' >&2
    printf '  %bInterrupted.%b Nothing was installed.\n' "${C_YELLOW}" "${C_RESET}" >&2
    exit 130
}
trap interrupted INT TERM

cleanup() {
    [ -n "${TMP_DOWNLOAD:-}" ] && [ -e "${TMP_DOWNLOAD}" ] && rm -f "$TMP_DOWNLOAD"
    [ -n "${TMP_SUMS:-}" ]     && [ -e "${TMP_SUMS}" ]     && rm -f "$TMP_SUMS"
    [ -n "${TMP_DIR:-}" ]      && [ -d "${TMP_DIR}" ]      && rmdir "$TMP_DIR" 2>/dev/null
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
# Parsing uses plain `read` field splitting - no sed/awk ERE, which would
# need non-POSIX extensions (lazy quantifiers) to do this correctly.

# get_expected_hash <sums-file> <file-name>  -> prints hash, or returns 1
get_expected_hash() {
    _sums="$1"; _want="$2"
    [ -s "$_sums" ] || return 1
    # The installer sets a global IFS of newline+tab, so `read` gets an
    # explicit space+tab IFS here - SHA256SUMS.txt separates fields with
    # spaces, which must still split.
    while IFS=$' \t' read -r _hash _name _extra || [ -n "${_hash:-}" ]; do
        [ -n "${_hash:-}" ] || continue
        case "$_hash" in '#'*) continue ;; esac   # comment lines
        # The hash field must be exactly 64 hex digits.
        printf '%s' "$_hash" | grep -Eq '^[0-9A-Fa-f]{64}$' || continue
        [ -n "${_name:-}" ] || continue
        _name="${_name##*\*}"                      # strip any binary-mode marker(s)
        _name="${_name%$'\r'}"                     # strip CR from CRLF files
        if [ "$_name" = "$_want" ]; then
            printf '%s' "$(printf '%s' "$_hash" | tr 'A-F' 'a-f')"
            return 0
        fi
    done < "$_sums"
    return 1
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        return 1
    fi
}

# --- fetch helper -------------------------------------------------------------
fetch() {  # fetch <url> <dest>
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 2 --connect-timeout 15 -A "$USER_AGENT" -o "$2" "$1" || return 1
    elif command -v wget >/dev/null 2>&1; then
        wget -q --tries=2 --timeout=30 -U "$USER_AGENT" -O "$2" "$1" || return 1
    else
        return 127
    fi
}

# --- main ---------------------------------------------------------------------
WHEEL_NAME="seedcode_cli-${VERSION}-py3-none-any.whl"
RELEASE_BASE="https://github.com/${REPO}/releases/download/v${VERSION}"
WHEEL_URL="${RELEASE_BASE}/${WHEEL_NAME}"
SUMS_URL="${RELEASE_BASE}/SHA256SUMS.txt"

printf '\n'
title "Seed Code CLI"
rule
ui "${C_GRAY}  Version ${VERSION}${C_RESET}"

section "${SYM_NODE} Checking system"
ok "Platform detected: $(uname -s) $(uname -m)"
# fetch() supports curl and wget; require at least one, don't insist on curl.
if command -v curl >/dev/null 2>&1; then
    ok "Download tool detected: curl"
elif command -v wget >/dev/null 2>&1; then
    ok "Download tool detected: wget"
else
    die "Neither curl nor wget was found in PATH. Install one and re-run."
fi
need python3
PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) \
    || die "python3 exists but could not report its version."
ok "Python detected: ${PY_VER}"
need pip3
ok "pip detected"

section "Downloading"
TMP_DIR=$(mktemp -d 2>/dev/null) || die "Could not create a temporary directory."
TMP_DOWNLOAD="${TMP_DIR}/${WHEEL_NAME}"
TMP_SUMS="${TMP_DIR}/SHA256SUMS.txt"

step "${SYM_DOT} ${WHEEL_NAME}"
fetch "$WHEEL_URL" "$TMP_DOWNLOAD" \
    || die "Could not download ${WHEEL_URL}"
ok "Package downloaded"

section "Verifying"
# 1) The release's SHA256SUMS.txt is authoritative. 2) The pinned digest
# (verified against the published artifact) cross-checks it, and acts as the
# expected value only when the release file is unreachable. Mismatch = abort;
# missing = abort. Verification is never bypassed.
fetch "$SUMS_URL" "$TMP_SUMS" || :   # optional; pinned digest covers this case
# Single capture: calling the function twice would print the first result
# to stdout (get_expected_hash's output IS the hash).
EXPECTED=$(get_expected_hash "$TMP_SUMS" "$WHEEL_NAME" || true)
if [ -n "$EXPECTED" ] && [ "$EXPECTED" != "$PINNED_WHEEL" ]; then
    die "The release's SHA256SUMS.txt and this installer's pinned checksum disagree for ${WHEEL_NAME}. Aborting rather than installing a possibly tampered package."
fi
if [ -z "$EXPECTED" ]; then
    EXPECTED="$PINNED_WHEEL"
    note "  (release SHA256SUMS.txt unreachable - using the installer's pinned checksum)"
fi
ACTUAL=$(sha256_of "$TMP_DOWNLOAD") || die "No SHA-256 tool available (sha256sum/shasum); refusing to install an unverified package."
if [ "$ACTUAL" != "$EXPECTED" ]; then
    die "Checksum mismatch for ${WHEEL_NAME}.
      expected ${EXPECTED}
      actual   ${ACTUAL}"
fi
ok "Checksum verified"

section "Installing"
if command -v pipx >/dev/null 2>&1; then
    # pipx is the cleanest per-user home: isolated venv, `seedcode` on PATH.
    if [ "$FORCE" = "1" ]; then
        pipx install --force "$TMP_DOWNLOAD" >/dev/null \
            || die "pipx install failed. Re-run with output visible: pipx install ${TMP_DOWNLOAD}"
    else
        pipx install "$TMP_DOWNLOAD" >/dev/null \
            || die "pipx install failed. Re-run with output visible: pipx install ${TMP_DOWNLOAD}"
    fi
    ok "Seed Code CLI installed (pipx)"
else
    # PEP 668 (externally managed environments, Debian 12+/newer distros)
    # blocks plain `pip install --user`; --break-system-packages is the
    # sanctioned escape hatch and lands in the same user site.
    if ! pip3 install --user "$TMP_DOWNLOAD" >/dev/null 2>&1; then
        if pip3 install --user --break-system-packages "$TMP_DOWNLOAD" >/dev/null 2>&1; then
            :
        else
            die "pip install failed. Install pipx (python3 -m pip install --user pipx) and re-run this installer."
        fi
    fi
    ok "Seed Code CLI installed"
fi

# Make sure a `seedcode` command exists somewhere the user can reach. The
# wheel's console script already lands in ~/.local/bin with pipx (and usually
# with pip --user) - in that case leave it alone. Only when nothing resolves
# do we create a direct shim, so we never overwrite pipx's entry point.
section "Verifying"
if command -v seedcode >/dev/null 2>&1; then
    SEEDCODE_BIN="$(command -v seedcode)"
else
    PYTHON_BIN="$(command -v python3)"
    mkdir -p "$BIN_DIR" 2>/dev/null || true
    printf '#!/usr/bin/env bash\nexec "%s" -m seedcode "$@"\n' "$PYTHON_BIN" > "${BIN_DIR}/seedcode"
    chmod +x "${BIN_DIR}/seedcode" 2>/dev/null || true
    SEEDCODE_BIN="${BIN_DIR}/seedcode"
fi
REPORTED="$("$SEEDCODE_BIN" --version 2>/dev/null | tail -n1)"
case "$REPORTED" in
    *"$VERSION"*) ok "seedcode --version  ->  ${REPORTED}" ;;
    *) die "The installed binary did not report version ${VERSION} (it said: '${REPORTED:-nothing}')." ;;
esac

# PATH guidance: honest about what actually works in THIS session.
case ":${PATH}:" in
    *":${BIN_DIR}:"*) PATH_OK=1 ;;
    *) PATH_OK=0 ;;
esac

printf '\n'
rule
ui "${C_GREEN}  ${SYM_TICK} Installation complete${C_RESET}"
printf '\n'
if [ "$PATH_OK" = "1" ]; then
    ui "${C_GRAY}  Run:${C_RESET}"
    ui "${C_GRAY}      seedcode${C_RESET}"
else
    ui "${C_GRAY}  Restart your terminal (or: export PATH=\"${BIN_DIR}:\$PATH\"), then run:${C_RESET}"
    ui "${C_GRAY}      seedcode${C_RESET}"
fi
ui "${C_DGRAY}  License: PolyForm Noncommercial License 1.0.0${C_RESET}"
printf '\n'
