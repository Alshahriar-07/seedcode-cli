#!/usr/bin/env bash
# ==========================================================================
#  Seed Code - macOS installer
#  * installs Python 3.12+ via Homebrew if missing
#  * creates an isolated virtualenv and installs Seed Code into it
#  * exposes a global `seedcode` command
#  * verifies the installation and reports success/failure
# ==========================================================================
set -euo pipefail

APP_NAME="Seed Code"
MIN_MAJOR=3
MIN_MINOR=12
VENV_DIR="${HOME}/Library/Application Support/SeedCode/venv"
LOG_FILE="${TMPDIR:-/tmp}/seedcode-install.log"

# --- Resolve the repository root (two levels up from this script) ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# --- Choose a writable location for the global launcher --------------------
# /usr/local/bin exists on Intel Macs and is user-writable with Homebrew;
# Apple Silicon uses /opt/homebrew/bin. Prefer whichever is on PATH & writable.
if [[ -w "/usr/local/bin" ]]; then
    LAUNCHER="/usr/local/bin/seedcode"
elif [[ -d "/opt/homebrew/bin" && -w "/opt/homebrew/bin" ]]; then
    LAUNCHER="/opt/homebrew/bin/seedcode"
else
    LAUNCHER="${HOME}/.local/bin/seedcode"
fi

# --- Logging + coloured output --------------------------------------------
if [[ -t 1 ]]; then
    C_GREEN=$'\033[0;32m'; C_RED=$'\033[0;31m'; C_YELLOW=$'\033[0;33m'; C_OFF=$'\033[0m'
else
    C_GREEN=""; C_RED=""; C_YELLOW=""; C_OFF=""
fi
log()  { echo "${C_GREEN}[INFO]${C_OFF} $*"  | tee -a "${LOG_FILE}"; }
warn() { echo "${C_YELLOW}[WARN]${C_OFF} $*" | tee -a "${LOG_FILE}"; }
err()  { echo "${C_RED}[ERROR]${C_OFF} $*"   | tee -a "${LOG_FILE}" >&2; }

trap 'err "Installation failed at line ${LINENO}. See ${LOG_FILE}."; exit 1' ERR

# --- Detect a suitable Python interpreter ---------------------------------
py_ok() {
    "$1" - <<'PYEOF' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)
PYEOF
}

find_python() {
    for cand in python3.13 python3.12 python3; do
        if command -v "$cand" >/dev/null 2>&1 && py_ok "$cand"; then
            command -v "$cand"; return 0
        fi
    done
    return 1
}

# --- Install Python via Homebrew ------------------------------------------
install_python() {
    log "Python ${MIN_MAJOR}.${MIN_MINOR}+ not found - installing via Homebrew."
    if ! command -v brew >/dev/null 2>&1; then
        err "Homebrew is required. Install it from https://brew.sh then re-run."
        exit 1
    fi
    brew update
    brew install python@3.12
}

main() {
    : > "${LOG_FILE}"
    echo "============================================================"
    echo "  ${APP_NAME} Installer (macOS)"
    echo "============================================================"
    log "Repository: ${REPO_ROOT}"
    log "Log file:   ${LOG_FILE}"

    local PYTHON
    if ! PYTHON="$(find_python)"; then
        install_python
        PYTHON="$(find_python)" || { err "Python ${MIN_MAJOR}.${MIN_MINOR}+ still unavailable."; exit 1; }
    fi
    log "Using Python: ${PYTHON} ($(${PYTHON} --version 2>&1))"

    # --- Create an isolated virtualenv ------------------------------------
    log "Creating virtual environment at ${VENV_DIR}"
    "${PYTHON}" -m venv "${VENV_DIR}"
    local VENV_PY="${VENV_DIR}/bin/python"

    # --- Install dependencies + Seed Code ---------------------------------
    log "Upgrading pip"
    "${VENV_PY}" -m pip install --upgrade pip >>"${LOG_FILE}" 2>&1
    if [[ -f "${REPO_ROOT}/requirements.txt" ]]; then
        log "Installing dependencies from requirements.txt"
        "${VENV_PY}" -m pip install -r "${REPO_ROOT}/requirements.txt" >>"${LOG_FILE}" 2>&1
    fi
    log "Installing ${APP_NAME}"
    "${VENV_PY}" -m pip install "${REPO_ROOT}" >>"${LOG_FILE}" 2>&1

    # --- Create the global launcher ---------------------------------------
    log "Creating global command: ${LAUNCHER}"
    mkdir -p "$(dirname "${LAUNCHER}")"
    cat > "${LAUNCHER}" <<EOF
#!/usr/bin/env bash
# Seed Code launcher - runs the CLI from its dedicated virtualenv.
exec "${VENV_DIR}/bin/seedcode" "\$@"
EOF
    chmod 0755 "${LAUNCHER}"

    # --- Verify -----------------------------------------------------------
    log "Verifying installation"
    "${VENV_PY}" -c "import seedcode; print('Seed Code', seedcode.__version__)" >>"${LOG_FILE}" 2>&1
    if command -v seedcode >/dev/null 2>&1; then
        log "'seedcode' resolves to: $(command -v seedcode)"
    else
        warn "'seedcode' is not on PATH. Add $(dirname "${LAUNCHER}") to your PATH."
    fi

    echo
    echo "${C_GREEN}[SUCCESS]${C_OFF} ${APP_NAME} installed. Run 'seedcode' to start."
}

main "$@"
