"""Generate the embedded default OpenRouter key module (v9.1.1 release builds).

Reads the built-in credential from the repository-root ``.env`` (or an
explicit ``--env-file``) — either of the two documented variable names,
``OPENROUTER_API_KEY`` or ``SEEDCODE_DEFAULT_API_KEY`` — and writes
``seedcode/_default_key.py``: a
git-ignored Python module that ``seedcode/default_api.py`` imports at
runtime. PyInstaller packages the generated module into the frozen
executable, so the released EXE/installer works out of the box.

Security rules enforced here:

* the generated module is written to ``seedcode/_default_key.py``, which the
  repository ``.gitignore`` excludes — it must never be committed;
* the script never prints the key, its length, or any fragment of it;
* ``--clean`` removes the generated file (used after packaging if a caller
  wants the working tree pristine; the build keeps it by default so the
  pip/wheel path is unaffected and the artifact stays reproducible).

The generated file carries a low-value obfuscation (XOR + base64) purely to
avoid a plaintext ``sk-or-...`` string sitting in a temp extraction
directory; it is NOT cryptographic protection. The long-term plan is a
server-side gateway, after which this module disappears.

Usage:
    python scripts/windows/embed_default_key.py            # generate from .env
    python scripts/windows/embed_default_key.py --clean    # remove the module
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = REPO_ROOT / ".env"
TARGET = REPO_ROOT / "seedcode" / "_default_key.py"

# Variables that may carry the built-in credential, in priority order. This
# MUST match the names the runtime reads (seedcode/default_api.BUILTIN_KEY_ENVS,
# seedcode.defaults.DEFAULT_API_ENV): a build configured with either documented
# name has to embed a key, or the installed artifact ships without one and
# reports "built-in connection is not available" on a clean machine.
KEY_ENV_NAMES: tuple[str, ...] = ("OPENROUTER_API_KEY", "SEEDCODE_DEFAULT_API_KEY")

# Names that look like a key variable but are NOT one. A typo'd name used to
# fail silently (build "succeeded", install had no credential); now it is
# reported as a configuration error with the exact fix.
_TYPO_NAMES = ("DEFAULT_API_KEY", "DEFULT_API_KEY", "SEEDC0DE_DEFAULT_API_KEY")

# A plausible OpenRouter/OpenAI-compatible key: no whitespace, a real length.
_MIN_KEY_LEN = 16

# XOR mask for the stored payload (obfuscation only, not security).
# Tied to the canonical version so a generated module can be traced to the
# build that produced it.
_MASK = b"SeedCode/v9.1.1"


def _obfuscate(key: str) -> str:
    raw = key.encode("utf-8")
    masked = bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(raw))
    return base64.b64encode(masked).decode("ascii")


def _read_env_value(env_file: Path, name: str) -> str:
    """Extract one variable from a .env file without echoing it."""
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        var, _, value = line.partition("=")
        if var.strip() == name:
            value = value.strip().strip("'\"")
            return value
    return ""


def _invalid_reason(key: str) -> str:
    """Why a candidate value is not a usable key ('' when it looks fine).

    Distinguishes "no configuration" from "invalid configuration" so a broken
    build says which one it is instead of the same generic message. Never
    includes the value itself.
    """
    if not key:
        return ""
    if any(ch.isspace() for ch in key):
        return "contains whitespace"
    if len(key) < _MIN_KEY_LEN:
        return f"shorter than {_MIN_KEY_LEN} characters"
    if not key.isascii():
        return "contains non-ASCII characters"
    return ""


def generate(env_file: Path) -> int:
    values = {name: _read_env_value(env_file, name) for name in KEY_ENV_NAMES}
    present = [(name, value) for name, value in values.items() if value]

    if not present:
        typos = [name for name in _TYPO_NAMES if _read_env_value(env_file, name)]
        print("[ERROR] No built-in credential variable found in %s" % env_file)
        print("        Set one of: %s" % ", ".join(KEY_ENV_NAMES))
        if typos:
            print(
                "        Found %s - that is not a recognised variable name."
                % ", ".join(typos)
            )
            print("        Rename it to %s." % KEY_ENV_NAMES[1])
        return 2

    key = ""
    for name, value in present:
        reason = _invalid_reason(value)
        if reason:
            print(
                "[ERROR] %s in %s looks invalid (%s); refusing to embed it."
                % (name, env_file, reason)
            )
            print("        Check the value in .env and re-run.")
            return 2
        if not key:
            key = value

    payload = _obfuscate(key)
    module = f'''"""Embedded default OpenRouter credential (v9.1.1 build-time artifact).

GENERATED by scripts/windows/embed_default_key.py from the build machine's
.env — DO NOT COMMIT (git-ignored) and DO NOT EDIT by hand. Decoded by
seedcode/default_api.py at runtime. The obfuscation is not a security
boundary; it only keeps the plaintext out of casual file dumps.
"""

from base64 import b64decode

_PAYLOAD = "{payload}"
_MASK = {_MASK!r}

DEFAULT_OPENROUTER_API_KEY = bytes(
    b ^ _MASK[i % len(_MASK)] for i, b in enumerate(b64decode(_PAYLOAD))
).decode("utf-8")
DEFAULT_API_ENABLED = True
DEFAULT_API_NOTE = "embedded Seed Code default API (v9.1.1 release build)"
'''
    TARGET.write_text(module, encoding="utf-8")
    # Never print the key; report only the safe fact that generation happened.
    print(f"[OK] Generated {TARGET} (embedded default key active for release builds)")
    print("     This file is git-ignored; do not commit it.")
    return 0


def clean() -> int:
    if TARGET.exists():
        TARGET.unlink()
        print(f"[OK] Removed {TARGET}")
    else:
        print(f"[INFO] {TARGET} not present (nothing to clean)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", default=str(DEFAULT_ENV), help="path to the .env file"
    )
    parser.add_argument(
        "--clean", action="store_true", help="remove the generated module"
    )
    opts = parser.parse_args()
    if opts.clean:
        return clean()
    env_file = Path(opts.env_file)
    if not env_file.exists():
        print(f"[ERROR] .env file not found: {env_file}")
        return 2
    return generate(env_file)


if __name__ == "__main__":
    sys.exit(main())
