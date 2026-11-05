#!/usr/bin/env bash
# Seed Code CLI v10.2.1 - official remote installer (Linux / macOS / WSL).
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
#   bash install.sh --version 10.2.1
#   bash install.sh --bin-dir ~/.local/bin
#
# Options:
#   --version V   Release version to install   (default: 10.2.1)
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
DEFAULT_VERSION="10.2.1"
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

# --- UI (monochrome premium theme, ANSI/Unicode with graceful fallback) -------
# Animation is enabled only on a real terminal. Redirected output, NO_COLOR,
# --plain and TERM=dumb all fall back to static, escape-free text. Every output
# helper is self-contained (no helper calls another), so the checksum and
# version harnesses that extract individual functions keep working.
if [ -t 1 ] && [ -z "${PLAIN:-}" ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
    UI_TTY=1
else
    UI_TTY=0
fi

# Width: COLUMNS, then tput, then a safe 80. Nothing GNU-only is required.
UI_WIDTH="${COLUMNS:-}"
case "$UI_WIDTH" in ''|*[!0-9]*) UI_WIDTH=80 ;; esac
[ "$UI_WIDTH" -ge 40 ] 2>/dev/null || UI_WIDTH=80
if [ "$UI_TTY" = "1" ] && command -v tput >/dev/null 2>&1; then
    _tput_width="$(tput cols 2>/dev/null || true)"
    case "$_tput_width" in
        ''|*[!0-9]*) : ;;
        *) [ "$_tput_width" -ge 40 ] && UI_WIDTH="$_tput_width" ;;
    esac
fi
[ "$UI_WIDTH" -le 100 ] || UI_WIDTH=100

if command -v locale >/dev/null 2>&1 && locale -k charmap 2>/dev/null | grep -qi 'UTF-8'; then
    UI_UNICODE=1
else
    UI_UNICODE=0
fi

if [ "$UI_UNICODE" = "1" ]; then
    SYM_RULE="─"; SYM_TICK="✓"; SYM_CROSS="✕"; SYM_NODE="●"; SYM_DOT="·"
    SYM_BRANCH="├"; SYM_FULL="█"; SYM_EMPTY="░"
else
    SYM_RULE="-"; SYM_TICK="OK"; SYM_CROSS="x"; SYM_NODE="*"; SYM_DOT="|"
    SYM_BRANCH="|"; SYM_FULL="#"; SYM_EMPTY="."
fi
RULE_TEXT=$(awk -v s="$SYM_RULE" 'BEGIN{for(i=0;i<38;i++) printf "%s", s}')

if [ "$UI_TTY" = "1" ]; then
    C_RESET=$'\033[0m'; C_WHITE=$'\033[97m'; C_GRAY=$'\033[90m'
    C_GREEN=$'\033[92m'; C_YELLOW=$'\033[93m'; C_RED=$'\033[91m'
    C_CYAN=$'\033[96m'; C_DGRAY=$'\033[37m'
else
    C_RESET=""; C_WHITE=""; C_GRAY=""; C_GREEN=""; C_YELLOW=""; C_RED=""
    C_CYAN=""; C_DGRAY=""
fi

STAGE_TOTAL=7
SPINNER_PID=""
if sleep 0.1 2>/dev/null; then SPINNER_DELAY=0.1; else SPINNER_DELAY=1; fi

show_banner() {
    printf '\n'
    if [ "$UI_WIDTH" -ge 56 ]; then
        while IFS= read -r _line; do
            printf '  %s%s%s\n' "${C_GRAY:-}" "$_line" "${C_RESET:-}"
        done <<'SEEDCODE_BANNER'
   _____                _  _____          _
  / ____|              | |/ ____|        | |
 | (___   ___  ___   __| | |     ___   __| | ___
  \___ \ / _ \/ _ \ / _` | |    / _ \ / _` |/ _ \
  ____) |  __/  __/ (_| | |___| (_) | (_| |  __/
 |_____/ \___|\___|\__,_|\_____\___/ \__,_|\___|
SEEDCODE_BANNER
    else
        printf '  %sSEED CODE CLI%s\n' "${C_WHITE:-}" "${C_RESET:-}"
    fi
    printf '  %sSEED CODE CLI%s  %s%s%s  %sAI CODING AGENT%s\n' \
        "${C_WHITE:-}" "${C_RESET:-}" "${C_DGRAY:-}" "${SYM_DOT:-|}" "${C_RESET:-}" "${C_GRAY:-}" "${C_RESET:-}"
    printf '  %sVersion %s%s\n\n' "${C_GRAY:-}" "${VERSION:-10.2.1}" "${C_RESET:-}"
}

write_status() {
    printf '  %s%s%s %s\n' "${C_GREEN:-}" "${SYM_NODE:-*}" "${C_RESET:-}" "$1"
}

write_success() {
    printf '  %s%s %s%s\n' "${C_GREEN:-}" "${SYM_TICK:-OK}" "$1" "${C_RESET:-}"
}

write_warning() {
    printf '  %s! %s%s\n' "${C_YELLOW:-}" "$1" "${C_RESET:-}" >&2
}

write_error() {
    printf '  %s%s %s%s\n' "${C_RED:-}" "${SYM_CROSS:-x}" "$1" "${C_RESET:-}" >&2
}

show_progress() {
    _cur="${1:-0}"
    _total="${2:-$STAGE_TOTAL}"
    _label="${3:-}"
    [ "$_total" -gt 0 ] 2>/dev/null || _total=1
    _bar_width=$((UI_WIDTH - 32))
    [ "$_bar_width" -gt 28 ] && _bar_width=28
    [ "$_bar_width" -lt 12 ] && _bar_width=12
    _filled=$((_bar_width * _cur / _total))
    _bar=""
    _i=0
    while [ "$_i" -lt "$_bar_width" ]; do
        if [ "$_i" -lt "$_filled" ]; then
            _bar="${_bar}${SYM_FULL}"
        else
            _bar="${_bar}${SYM_EMPTY}"
        fi
        _i=$((_i + 1))
    done
    printf '  [%s] %s/%s  %s\n' "$_bar" "$_cur" "$_total" "$_label"
}

stage() {
    show_progress "${1:-0}" "$STAGE_TOTAL" "${2:-}"
}

show_spinner() {
    _text="${1:-Working...}"
    shift
    if [ "$#" -eq 0 ]; then return 0; fi
    if [ "$UI_TTY" != "1" ]; then
        "$@" >/dev/null 2>&1
        return $?
    fi
    "$@" >/dev/null 2>&1 &
    SPINNER_PID=$!
    _frames='|/-\'
    _i=0
    while kill -0 "$SPINNER_PID" 2>/dev/null; do
        _frame="${_frames:$((_i % 4)):1}"
        printf '\r  %s%s%s %s' "${C_GREEN:-}" "$_frame" "${C_RESET:-}" "$_text"
        _i=$((_i + 1))
        sleep "$SPINNER_DELAY"
    done
    wait "$SPINNER_PID"
    _rc=$?
    SPINNER_PID=""
    printf '\r%*s\r' "$UI_WIDTH" ""
    return $_rc
}

cleanup() {
    if [ -n "${SPINNER_PID:-}" ]; then
        kill "$SPINNER_PID" 2>/dev/null || true
        SPINNER_PID=""
    fi
    if [ "${UI_TTY:-0}" = "1" ]; then
        printf '\033[?25h'
    fi
    if [ -n "${TMP_DIR:-}" ] && [ -d "${TMP_DIR}" ]; then
        rm -rf "${TMP_DIR}" 2>/dev/null || true
    fi
    return 0
}

interrupted() {
    if [ -n "${SPINNER_PID:-}" ]; then
        kill "$SPINNER_PID" 2>/dev/null || true
    fi
    if [ "${UI_TTY:-0}" = "1" ]; then
        printf '\033[?25h'
    fi
    printf '\n'
    printf '  %sInterrupted.%s Nothing was installed.\n\n' "${C_YELLOW:-}" "${C_RESET:-}" >&2
    exit 130
}

# Compatibility helpers used by the installation logic below.
ui()      { printf '  %b\n' "$1"; }
title()   { printf '  %s%s%s\n' "${C_WHITE:-}" "$1" "${C_RESET:-}"; }
rule()    { printf '  %s%s%s\n' "${C_DGRAY:-}" "$RULE_TEXT" "${C_RESET:-}"; }
section() { printf '\n'; printf '  %s%s%s\n' "${C_WHITE:-}" "$1" "${C_RESET:-}"; }
ok()      { printf '  %s%s %s%s\n' "${C_GREEN:-}" "${SYM_TICK:-OK}" "$1" "${C_RESET:-}"; }
step()    { printf '  %s%s %s%s\n' "${C_GRAY:-}" "${SYM_BRANCH:-|}" "$1" "${C_RESET:-}"; }
log()     { printf '  %s\n' "$1"; }
warn()    { printf '  %s! %s%s\n' "${C_YELLOW:-}" "$1" "${C_RESET:-}" >&2; }

die() {
    _msg="${1:-Installation failed.}"
    _code="${2:-INSTALL_FAILED}"
    case "$_code" in
        DOWNLOAD_FAILED)       _action="Check your internet connection and retry." ;;
        CHECKSUM_MISSING|CHECKSUM_MISMATCH) _action="The download may be corrupt or the release incomplete. Retry; if it persists, report it." ;;
        CHECKSUM_UNAVAILABLE)  _action="Check your internet connection and retry." ;;
        UNSUPPORTED_PLATFORM)  _action="Use a supported Linux, macOS, or WSL x64/arm64 system." ;;
        MISSING_DEPENDENCY)    _action="Install the missing tool and re-run the installer." ;;
        PERMISSION_DENIED)     _action="Choose a writable --bin-dir or fix its permissions." ;;
        VERIFY_FAILED)         _action="Remove any older seedcode on PATH and retry the installation." ;;
        *)                     _action="Retry the installation. If it keeps failing, report it with the error code." ;;
    esac
    printf '\n' >&2
    printf '  %s%s Installation failed%s\n\n' "${C_RED:-}" "${SYM_CROSS:-x}" "${C_RESET:-}" >&2
    printf '  Reason:\n  %b\n\n' "$_msg" >&2
    printf '  Suggested action:\n  %s\n\n' "$_action" >&2
    printf '  Error code: %s\n\n' "$_code" >&2
    printf '  Download manually from: https://github.com/%s/releases\n\n' "${REPO:-Alshahriar-07/seedcode-cli}" >&2
    exit 1
}

trap interrupted INT TERM
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
# version_ok <reported-output> <version> -> 0 when the reported output carries
# the version as a whole token. The historical check was a plain substring
# match (`*"$VERSION"*`), which a wrongly-numbered binary defeats: "Seed Code
# CLI 19.3.2" contains "9.3.2". This matcher requires the version at a token
# boundary — never directly after a digit ('19.3.2' != '9.3.2'), never directly
# before a digit or a dot ('9.3.2' != '9.3.21' / '9.3.2.1'), with only a
# single-occurrence v/V version marker ('v9.3.2' is accepted; 'rev9.3.2' is
# not). The same rules are implemented in install.ps1 and setup.iss.
version_ok() {
    awk -v want="${2:-}" '
        {
            t = $0
            n = length(t)
            m = length(want)
            if (m == 0 || n < m) next
            for (i = 1; i + m - 1 <= n; i++) {
                if (substr(t, i, m) != want) continue
                b = (i > 1) ? substr(t, i - 1, 1) : " "
                a = (i + m <= n) ? substr(t, i + m, 1) : " "
                if (b ~ /[0-9.]/) continue
                if (b ~ /[A-Za-z]/) {
                    if (b != "v" && b != "V") continue
                    p = (i > 2) ? substr(t, i - 2, 1) : " "
                    if (p ~ /[A-Za-z]/) continue
                }
                if (a ~ /[0-9.]/) continue
                found = 1
                break
            }
        }
        END { exit found ? 0 : 1 }
    ' <<< "${1:-}"
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
        die "No SHA256 checksum for ${_name} in SHA256SUMS.txt - refusing to install an unverified artifact." "CHECKSUM_MISSING"
    fi
    if ! _actual="$(sha256_of "$_file")"; then
        die "No SHA-256 tool available (sha256sum/shasum/openssl) - refusing to install an unverified artifact." "CHECKSUM_FAILED"
    fi
    if [ "$_actual" != "$_expected" ]; then
        die "Checksum mismatch for ${_name}.
      expected ${_expected}
      actual   ${_actual}" "CHECKSUM_MISMATCH"
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
show_banner
ui "${C_DGRAY:-}  Initializing installation...${C_RESET:-}"

stage 1 "Checking system requirements"
need uname
case "$(uname -s)" in
    Linux)  OS="linux" ;;
    Darwin) OS="darwin" ;;
    *)      die "Unsupported operating system '$(uname -s)'. Seed Code ships Linux and macOS builds." "UNSUPPORTED_PLATFORM" ;;
esac
case "$(uname -m)" in
    x86_64|amd64) ARCH="x64" ;;
    arm64|aarch64) ARCH="arm64" ;;
    *) die "Unsupported architecture '$(uname -m)'. Seed Code ships x64 and arm64 builds." "UNSUPPORTED_PLATFORM" ;;
esac
BINARY_ASSET="SeedCode-CLI-${VERSION}-${OS}-${ARCH}"
ok "Platform detected: ${OS} ${ARCH}"

stage 2 "Detecting environment"
if command -v curl >/dev/null 2>&1; then
    ok "Download tool detected: curl"
elif command -v wget >/dev/null 2>&1; then
    ok "Download tool detected: wget"
else
    die "Neither curl nor wget was found in PATH. Install one and re-run." "MISSING_DEPENDENCY"
fi
TMP_DIR=$(mktemp -d 2>/dev/null) || die "Could not create a temporary directory." "ENVIRONMENT_FAILED"

stage 3 "Preparing installation directory"
INSTALL_DIR="$BIN_DIR"
INSTALLED_EXE="${INSTALL_DIR}/${BIN_NAME}"
mkdir -p "${INSTALL_DIR}" 2>/dev/null || die "Could not create ${INSTALL_DIR}. Choose a writable --bin-dir." "PERMISSION_DENIED"
ok "Install directory ready: ${INSTALL_DIR}"

stage 4 "Downloading SeedCode CLI"
step "${BINARY_ASSET}"
if fetch_file "${RELEASE_BASE}/${BINARY_ASSET}" "${TMP_DIR}/${BINARY_ASSET}"; then
    TARGET_KIND="binary"
    TARGET_ASSET="${BINARY_ASSET}"
    ok "Standalone binary downloaded"
else
    # No native build for this platform in this release: use the wheel, which
    # is the portable artifact every release publishes.
    write_warning "Standalone binary unavailable for ${OS} ${ARCH}; using the Python wheel."
    TARGET_KIND="wheel"
    TARGET_ASSET="${WHEEL_ASSET}"
    step "${WHEEL_ASSET}"
    fetch_file "${RELEASE_BASE}/${WHEEL_ASSET}" "${TMP_DIR}/${WHEEL_ASSET}" \
        || die "Could not download ${WHEEL_ASSET} from ${RELEASE_BASE}" "DOWNLOAD_FAILED"
    ok "Package downloaded"
fi

stage 5 "Verifying package integrity"
TMP_SUMS="${TMP_DIR}/SHA256SUMS.txt"
fetch_file "$SUMS_URL" "$TMP_SUMS" \
    || die "Could not download SHA256SUMS.txt from ${SUMS_URL} - refusing to install an unverified artifact." "CHECKSUM_UNAVAILABLE"
SUMS_TEXT="$(cat "$TMP_SUMS")"
# verify_sha256 aborts on a missing entry or a mismatch; verification is never
# bypassed and there is no "skip" path. It is called on whichever artifact was
# downloaded, immediately before that artifact is installed.
if [ "${TARGET_KIND}" = "binary" ]; then
    verify_sha256 "${TMP_DIR}/${BINARY_ASSET}" "$SUMS_TEXT" "${BINARY_ASSET}"
else
    verify_sha256 "${TMP_DIR}/${WHEEL_ASSET}" "$SUMS_TEXT" "${WHEEL_ASSET}"
fi

stage 6 "Installing SeedCode CLI"
if [ "${TARGET_KIND}" = "binary" ]; then
    install -m 0755 "${TMP_DIR}/${BINARY_ASSET}" "${INSTALLED_EXE}" \
        || die "Could not install ${BINARY_ASSET} to ${INSTALLED_EXE}." "PERMISSION_DENIED"
    ok "Seed Code CLI installed (standalone)"
else
    need python3
    PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) \
        || die "python3 exists but could not report its version." "ENVIRONMENT_FAILED"
    ok "Python detected: ${PY_VER}"
    if command -v pipx >/dev/null 2>&1; then
        if [ "$FORCE" = "1" ]; then
            show_spinner "Installing with pipx..." pipx install --force "${TMP_DIR}/${WHEEL_ASSET}" \
                || die "pipx install failed. Re-run with output visible: pipx install ${TMP_DIR}/${WHEEL_ASSET}" "INSTALL_FAILED"
        else
            show_spinner "Installing with pipx..." pipx install "${TMP_DIR}/${WHEEL_ASSET}" \
                || die "pipx install failed. Re-run with output visible: pipx install ${TMP_DIR}/${WHEEL_ASSET}" "INSTALL_FAILED"
        fi
        ok "Seed Code CLI installed (pipx)"
    else
        need pip3
        # PEP 668 (externally managed environments, Debian 12+/newer distros)
        # blocks plain `pip install --user`; --break-system-packages is the
        # sanctioned escape hatch and lands in the same user site.
        if ! show_spinner "Installing with pip..." pip3 install --user "${TMP_DIR}/${WHEEL_ASSET}"; then
            show_spinner "Installing with pip (user site)..." pip3 install --user --break-system-packages "${TMP_DIR}/${WHEEL_ASSET}" \
                || die "pip install failed. Install pipx (python3 -m pip install --user pipx) and re-run this installer." "INSTALL_FAILED"
        fi
        ok "Seed Code CLI installed (pip --user)"
        if [ ! -e "${INSTALLED_EXE}" ]; then
            PYTHON_BIN="$(command -v python3)"
            printf '#!/usr/bin/env bash\nexec "%s" -m seedcode "$@"\n' "$PYTHON_BIN" > "${INSTALLED_EXE}"
            chmod +x "${INSTALLED_EXE}" 2>/dev/null \
                || die "Could not make ${INSTALLED_EXE} executable." "PERMISSION_DENIED"
        fi
    fi
fi

stage 7 "Verifying installation"
# Verify the file THIS run installed, by absolute path - a `seedcode` already
# on PATH could be an older copy, and a stale binary must never be reported as
# this install's success.
REPORTED="$("$INSTALLED_EXE" --version 2>/dev/null | tail -n1)"
if version_ok "${REPORTED:-}" "$VERSION"; then
    ok "seedcode --version  ->  ${REPORTED}"
else
    die "The installed binary did not report version ${VERSION} (it said: '${REPORTED:-nothing}')." "VERIFY_FAILED"
fi

# A different `seedcode` earlier on PATH keeps being invoked instead of the one
# just installed (the "old executable still runs" failure mode). Say so out
# loud instead of letting the user believe the upgrade took effect.
ON_PATH="$(command -v seedcode 2>/dev/null || true)"
if [ -n "$ON_PATH" ] && [ "$ON_PATH" != "$INSTALLED_EXE" ] && [ "$ON_PATH" != "$(readlink -f "$INSTALLED_EXE" 2>/dev/null || printf '%s' "$INSTALLED_EXE")" ]; then
    printf '\n'
    printf '  %s! Another seedcode is earlier on PATH:%s\n' "${C_YELLOW:-}" "${C_RESET:-}"
    printf '  %s      %s%s\n' "${C_DGRAY:-}" "$ON_PATH" "${C_RESET:-}"
    printf '  %s      (this install: %s)%s\n' "${C_DGRAY:-}" "$INSTALLED_EXE" "${C_RESET:-}"
    printf '  %s    Remove the older copy, or run this one directly:%s\n' "${C_DGRAY:-}" "${C_RESET:-}"
    printf '  %s      %s --version%s\n' "${C_DGRAY:-}" "$INSTALLED_EXE" "${C_RESET:-}"
fi

# PATH guidance: honest about what actually works in THIS session.
case ":${PATH}:" in
    *":${INSTALL_DIR}:"*) PATH_OK=1 ;;
    *) PATH_OK=0 ;;
esac

printf '\n'
rule
printf '  %s%s Seed Code CLI installed successfully!%s\n\n' "${C_GREEN:-}" "${SYM_TICK:-OK}" "${C_RESET:-}"
printf '  %sVersion: %s%s\n\n' "${C_GRAY:-}" "$VERSION" "${C_RESET:-}"
printf '  %sGet started:%s\n\n' "${C_GRAY:-}" "${C_RESET:-}"
if [ "$PATH_OK" = "1" ]; then
    printf '      %sseedcode --version%s\n' "${C_WHITE:-}" "${C_RESET:-}"
    printf '      %sseedcode%s\n\n' "${C_WHITE:-}" "${C_RESET:-}"
else
    printf '      %sexport PATH="%s:$PATH"%s\n' "${C_WHITE:-}" "$INSTALL_DIR" "${C_RESET:-}"
    printf '      %sseedcode --version%s\n' "${C_WHITE:-}" "${C_RESET:-}"
    printf '      %sseedcode%s\n\n' "${C_WHITE:-}" "${C_RESET:-}"
fi
printf '  %sDocumentation:%s\n' "${C_GRAY:-}" "${C_RESET:-}"
printf '  %shttps://seedcode-cli.vercel.app/%s\n\n' "${C_DGRAY:-}" "${C_RESET:-}"
printf '  %sHappy coding!%s\n\n' "${C_GREEN:-}" "${C_RESET:-}"
