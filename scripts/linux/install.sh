#!/usr/bin/env bash
# ==========================================================================
#  Seed Code - Linux installer
#  * installs Python 3.12+ if missing (via the system package manager)
#  * creates an isolated virtualenv and installs Seed Code into it
#  * exposes a global `seedcode` command
#  * verifies the installation and reports success/failure
# ==========================================================================
set -euo pipefail

APP_NAME="Seed Code"
MIN_MAJOR=3
MIN_MINOR=12
VENV_DIR="/usr/local/lib/seedcode/venv"
LAUNCHER="/usr/local/bin/seedcode"
LOG_FILE="${TMPDIR:-/tmp}/seedcode-install.log"

# --- Resolve the repository root (two levels up from this script) ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# --- Logging + coloured output --------------------------------------------
if [[ -t 1 ]]; then
    C_GREEN=$'\033[0;32m'; C_RED=$'\033[0;31m'; C_YELLOW=$'\033[0;33m'; C_OFF=$'\033[0m'
else
    C_GREEN=""; C_RED=""; C_YELLOW=""; C_OFF=""
fi
log()  { echo "${C_GREEN}[INFO]${C_OFF} $*"  | tee -a "${LOG_FILE}"; }
warn() { echo "${C_YELLOW}[WARN]${C_OFF} $*" | tee -a "${LOG_FILE}"; }
err()  { echo "${C_RED}[ERROR]${C_OFF} $*"   | tee -a "${LOG_FILE}" >&2; }

# Fail loudly with the line number so problems are easy to trace.
trap 'err "Installation failed at line ${LINENO}. See ${LOG_FILE}."; exit 1' ERR

# --- Privilege escalation helper ------------------------------------------
SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        # No sudo: fall back to a per-user location.
        VENV_DIR="${HOME}/.local/share/seedcode/venv"
        LAUNCHER="${HOME}/.local/bin/seedcode"
        warn "Running without root; installing to ${HOME}/.local."
    fi
fi

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

# --- Install Python via the available package manager ----------------------
install_python() {
    log "Python ${MIN_MAJOR}.${MIN_MINOR}+ not found - attempting to install it."
    if command -v apt-get >/dev/null 2>&1; then
        ${SUDO} apt-get update -y
        ${SUDO} apt-get install -y python3 python3-venv python3-pip
    elif command -v dnf >/dev/null 2>&1; then
        ${SUDO} dnf install -y python3 python3-pip
    elif command -v pacman >/dev/null 2>&1; then
        ${SUDO} pacman -Sy --noconfirm python python-pip
    elif command -v zypper >/dev/null 2>&1; then
        ${SUDO} zypper install -y python3 python3-pip
    else
        err "No supported package manager found. Install Python ${MIN_MAJOR}.${MIN_MINOR}+ manually."
        exit 1
    fi
}

main() {
    : > "${LOG_FILE}"
    echo "============================================================"
    echo "  ${APP_NAME} Installer (Linux)"
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
    ${SUDO} "${PYTHON}" -m venv "${VENV_DIR}"
    local VENV_PY="${VENV_DIR}/bin/python"

    # --- Install dependencies + Seed Code ---------------------------------
    log "Upgrading pip"
    ${SUDO} "${VENV_PY}" -m pip install --upgrade pip >>"${LOG_FILE}" 2>&1
    if [[ -f "${REPO_ROOT}/requirements.txt" ]]; then
        log "Installing dependencies from requirements.txt"
        ${SUDO} "${VENV_PY}" -m pip install -r "${REPO_ROOT}/requirements.txt" >>"${LOG_FILE}" 2>&1
    fi
    log "Installing ${APP_NAME}"
    ${SUDO} "${VENV_PY}" -m pip install "${REPO_ROOT}" >>"${LOG_FILE}" 2>&1

    # --- Create the global launcher ---------------------------------------
    log "Creating global command: ${LAUNCHER}"
    mkdir -p "$(dirname "${LAUNCHER}")" 2>/dev/null || ${SUDO} mkdir -p "$(dirname "${LAUNCHER}")"
    local TMP_LAUNCHER
    TMP_LAUNCHER="$(mktemp)"
    cat > "${TMP_LAUNCHER}" <<EOF
#!/usr/bin/env bash
# Seed Code launcher - runs the CLI from its dedicated virtualenv.
exec "${VENV_DIR}/bin/seedcode" "\$@"
EOF
    ${SUDO} install -m 0755 "${TMP_LAUNCHER}" "${LAUNCHER}"
    rm -f "${TMP_LAUNCHER}"

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
