"""Terminal-host, shell, and environment detection for Seed Code.

Seed Code must behave identically in Windows Terminal, the VS Code integrated
terminal (PowerShell *and* CMD), Git Bash, and plain shells — without ever
depending on one specific host. This module is the single place that reads the
environment and answers three questions:

* which terminal **host** renders us (VS Code, Windows Terminal, unknown);
* which **shell** the user is in (PowerShell, pwsh, CMD, Bash);
* whether interactive input/output is safe (a real TTY, a sane encoding).

Design rules:

* detection is **best-effort and pure** — it never raises, never asserts, and
  returns safe defaults when the environment tells us nothing;
* it never makes VS Code detection a hard dependency (an unknown host simply
  behaves like a normal CLI);
* everything is a pure function of an explicit environment mapping, so it is
  fully unit-testable without a real terminal.

Environment overrides (both optional):

* ``SEEDCODE_SHELL`` — force the detected shell (cmd | powershell | pwsh | bash).
* ``SEEDCODE_PLAIN`` — force plain (no-ANSI) rendering; useful when a host
  mangles escape sequences.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

# Terminal emulators we recognise. Anything else is "unknown" and is treated
# as a normal CLI host (never an error).
HOST_VSCODE = "vscode"
HOST_WINDOWS_TERMINAL = "windows-terminal"
HOST_UNKNOWN = "unknown"

# Shell names (the vocabulary the run_command tool understands, after mapping).
SHELL_POWERSHELL = "powershell"
SHELL_PWSH = "pwsh"
SHELL_CMD = "cmd"
SHELL_BASH = "bash"

_KNOWN_SHELLS = (SHELL_POWERSHELL, SHELL_PWSH, SHELL_CMD, SHELL_BASH)

# VS Code sets a subset of these in its integrated-terminal child shell; any
# one is enough to recognise it. None is required to exist.
_VSCODE_KEYS = (
    "VSCODE_PID",
    "VSCODE_CWD",
    "VSCODE_IPC_HOOK",
    "VSCODE_IPC_HOOK_CLI",
    "VSCODE_INJECTION",
    "VSCODE_GIT_IPC_HANDLE",
    "VSCODE_GIT_ASKPASS_NODE",
)

_DISABLED = {"", "0", "false", "no", "off"}


def _flag(env: Mapping[str, str], name: str) -> bool:
    """True when ``name`` is present and not an explicit 'off' value."""
    return (env.get(name) or "").strip().lower() not in _DISABLED


def detect_host(env: Mapping[str, str]) -> str:
    """Best-effort terminal-emulator detection (never raises)."""
    term_program = (env.get("TERM_PROGRAM") or "").strip().lower()
    if "vscode" in term_program:
        return HOST_VSCODE
    if any((env.get(key) or "").strip() for key in _VSCODE_KEYS):
        return HOST_VSCODE
    if (env.get("WT_SESSION") or "").strip() or (env.get("WT_PROFILE_ID") or "").strip():
        return HOST_WINDOWS_TERMINAL
    return HOST_UNKNOWN


def detect_shell(env: Mapping[str, str], *, platform: str | None = None) -> str:
    """Best-effort active-shell detection; ``""`` when it cannot be told.

    The result is one of the :data:`SHELL_*` constants or ``""``. ``SHELL``
    (POSIX) and ``MSYSTEM`` (Git Bash) are authoritative when present;
    otherwise Windows is inferred from PowerShell's ``PSModulePath`` or
    ``COMSPEC``.
    """
    platform = platform or sys.platform

    forced = (env.get("SEEDCODE_SHELL") or "").strip().lower()
    if forced:
        return SHELL_BASH if forced == "sh" else forced if forced in _KNOWN_SHELLS else ""

    # Git Bash / MSYS on Windows.
    if (env.get("MSYSTEM") or "").strip():
        return SHELL_BASH

    shell = (env.get("SHELL") or "").strip()
    if shell:
        base = shell.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if "pwsh" in base:
            return SHELL_PWSH
        if "powershell" in base:
            return SHELL_POWERSHELL
        if base in ("bash", "sh", "zsh", "dash") or "bash" in base:
            return SHELL_BASH

    if platform == "win32":
        ps_module_path = env.get("PSModulePath") or ""
        if ps_module_path:
            core = (env.get("POWERSHELL_DISTRIBUTION_CHANNEL") or "").strip()
            if core or "powershell\\7" in ps_module_path.lower() or "powershell/7" in ps_module_path.lower():
                return SHELL_PWSH
            return SHELL_POWERSHELL
        if (env.get("COMSPEC") or "").strip():
            return SHELL_CMD

    return ""


@dataclass(frozen=True, slots=True)
class TerminalEnv:
    """A snapshot of the terminal environment (pure data, no I/O)."""

    host: str
    shell: str
    is_vscode: bool
    is_windows_terminal: bool
    is_tty: bool
    plain: bool
    encoding: str
    columns: int

    @property
    def is_windows(self) -> bool:
        return sys.platform == "win32"

    def describe(self) -> str:
        """A one-line, secret-free summary for logs and diagnostics."""
        shell = self.shell or "unknown"
        tty = "tty" if self.is_tty else "no-tty"
        mode = "plain" if self.plain else "ansi"
        return f"host={self.host} shell={shell} {tty} {mode} {self.encoding} {self.columns}col"


def _terminal_columns(default: int = 80) -> int:
    """Current terminal width, or ``default`` when it cannot be determined."""
    try:
        return os.get_terminal_size().columns or default
    except (OSError, ValueError):
        return default


def detect_terminal(
    env: Mapping[str, str] | None = None,
    *,
    platform: str | None = None,
    isatty: bool | None = None,
    columns: int | None = None,
) -> TerminalEnv:
    """Build a :class:`TerminalEnv` from the environment (never raises)."""
    env = os.environ if env is None else env
    host = detect_host(env)
    shell = detect_shell(env, platform=platform)
    if isatty is None:
        try:
            isatty = bool(sys.stdout.isatty())
        except (AttributeError, ValueError):
            isatty = False
    term = (env.get("TERM") or "").strip().lower()
    plain = (env.get("SEEDCODE_PLAIN") or "").strip().lower() not in _DISABLED or term == "dumb"
    return TerminalEnv(
        host=host,
        shell=shell,
        is_vscode=host == HOST_VSCODE,
        is_windows_terminal=host == HOST_WINDOWS_TERMINAL,
        is_tty=isatty,
        plain=plain,
        encoding=(getattr(sys.stdout, "encoding", "") or "") or "utf-8",
        columns=columns if columns is not None else _terminal_columns(),
    )


def run_command_shell(
    env: Mapping[str, str] | None = None, *, platform: str | None = None
) -> str:
    """The shell key the ``run_command`` tool should use by default.

    Returns one of ``"cmd" | "powershell" | "bash"`` (the keys the terminal
    tool knows) or ``""`` to mean "let the platform decide" (``shell=True``).
    ``pwsh`` maps to ``powershell`` because both accept the same builder.
    """
    shell = detect_shell(env if env is not None else os.environ, platform=platform)
    if shell == SHELL_PWSH:
        return SHELL_POWERSHELL
    return shell if shell in (SHELL_CMD, SHELL_POWERSHELL, SHELL_BASH) else ""


def workspace_root() -> Path:
    """The directory Seed Code was launched in — the active workspace root.

    Deliberately a thin wrapper over :func:`os.getcwd` so there is exactly one
    definition of "where the user's project is"; nothing in startup ever
    changes the working directory, so this is stable for the whole session.
    """
    return Path.cwd()


__all__ = [
    "HOST_UNKNOWN",
    "HOST_VSCODE",
    "HOST_WINDOWS_TERMINAL",
    "SHELL_BASH",
    "SHELL_CMD",
    "SHELL_POWERSHELL",
    "SHELL_PWSH",
    "TerminalEnv",
    "detect_host",
    "detect_shell",
    "detect_terminal",
    "run_command_shell",
    "workspace_root",
]
