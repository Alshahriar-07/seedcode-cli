"""Terminal execution tool: run a shell command in the workspace.

Cross-platform via ``subprocess.run(shell=True)`` — cmd/PowerShell semantics
on Windows, /bin/sh on Linux and macOS. Output is captured (never inherits
the TTY, so it cannot corrupt the Rich UI) and time-boxed.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

from .base import ToolResult, register

if TYPE_CHECKING:
    from .permissions import PermissionManager

_DEFAULT_TIMEOUT_S = 60
_MAX_TIMEOUT_S = 300


def run_command(perm: "PermissionManager", command: str, timeout_s: int) -> ToolResult:
    """Shared runner (the git tool reuses it for real git invocations)."""
    timeout_s = max(1, min(int(timeout_s), _MAX_TIMEOUT_S))
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=perm.workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(False, f"Command timed out after {timeout_s}s: {command}")
    except OSError as exc:
        return ToolResult(False, f"Could not run command: {exc}")

    parts = []
    if proc.stdout:
        parts.append(proc.stdout.rstrip())
    if proc.stderr:
        parts.append(f"[stderr]\n{proc.stderr.rstrip()}")
    body = "\n".join(parts) or "(no output)"
    return ToolResult(proc.returncode == 0, f"exit code {proc.returncode}\n{body}")


@register(
    "run_command",
    "Run a shell command in the workspace and return its output.",
    {
        "command": "the shell command to run",
        "timeout": "(optional) seconds before the command is killed (default 60)",
    },
    mutates=True,
)
def _run_command(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    command = str(args["command"]).strip()
    if not command:
        return ToolResult(False, "Command is empty.")
    perm.check_execute(command)
    return run_command(perm, command, int(args.get("timeout", _DEFAULT_TIMEOUT_S)))
