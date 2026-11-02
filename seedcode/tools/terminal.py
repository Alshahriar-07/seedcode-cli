"""Terminal execution tool: run a shell command in the workspace, live.

Commands run through ``subprocess.Popen`` with a reader thread so output
streams line-by-line while the process is still running (Windows pipes have
no ``select``, hence the thread + queue). The agent layer can attach a
per-line callback via ``PermissionManager.on_output`` to echo progress to
the user as it happens.

Shell selection: ``cmd``, ``powershell`` and ``bash`` are supported
explicitly; the default ("") is the platform shell (cmd on Windows,
/bin/sh elsewhere) via ``shell=True``.

Cancellation: pressing Ctrl+C while a command is running kills that
command's whole process tree (``taskkill /T /F`` on Windows, process-group
SIGKILL elsewhere) and reports the cancellation as a failed tool result —
the agent turn itself continues, so the model learns the command was
cancelled instead of the loop dying.
"""

from __future__ import annotations

import itertools
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable

from .base import MAX_OUTPUT_CHARS, ToolResult, int_arg, register
from .permissions import CATEGORY_SHELL
from ..utils.terminal_env import run_command_shell
from ..utils.text import safe_text

if TYPE_CHECKING:
    from .permissions import PermissionManager

_DEFAULT_TIMEOUT_S = 60
_MAX_TIMEOUT_S = 300

# After the output pipe closes (normal: the child exited), wait at most this
# long for the process itself before reaping its tree. A child that closes
# stdout early and keeps running must never freeze the calling UI loop.
_EXIT_GRACE_S = 5.0

# Explicit shell -> argv builder. "" (default) uses shell=True instead.
# "pwsh" shares the PowerShell builder; "auto" resolves via shell detection.
_SHELLS: dict[str, Callable[[str], list[str]]] = {
    "cmd": lambda c: ["cmd", "/d", "/c", c],
    "powershell": lambda c: [
        "powershell", "-NoProfile", "-NonInteractive", "-Command", c
    ],
    "pwsh": lambda c: ["pwsh", "-NoProfile", "-NonInteractive", "-Command", c],
    "bash": lambda c: ["bash", "-lc", c],
}

# Values that mean "use the detected active shell" instead of a fixed one.
_AUTO_SHELLS = frozenset({"auto", "detect", "default"})

_EOF = object()  # sentinel the reader thread puts when the pipe closes


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process and all of its children; never raises."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _reader(pipe: IO[str], out: "queue.Queue[object]") -> None:
    """Pump lines from the child's merged stdout into the queue."""
    try:
        for line in pipe:
            out.put(line)
    except (OSError, ValueError):
        pass  # pipe closed mid-read; treat as EOF
    out.put(_EOF)


def _popen_spec(command: str, shell: str) -> tuple[str | list[str] | None, bool, str]:
    """Resolve a command + shell choice to a Popen specification.

    Returns ``(argv, use_shell, error)``. ``argv`` is None when the requested
    shell is unknown, in which case ``error`` is the user-facing reason. Shared
    by the foreground and background runners so the two can never disagree.
    """
    key = shell.strip().lower()
    if key in _AUTO_SHELLS:
        # Resolve the shell the user is actually in (VS Code PowerShell/CMD,
        # Git Bash, ...). Empty means "let the platform decide".
        key = run_command_shell()
    if key:
        builder = _SHELLS.get(key)
        if builder is None:
            choices = ", ".join(sorted(set(_SHELLS) | {"auto"}))
            return None, False, f"Unknown shell '{shell}'. Choose one of: {choices}."
        return builder(command), False, ""
    return command, True, ""


def run_command(
    perm: "PermissionManager",
    command: str,
    timeout_s: int,
    *,
    shell: str = "",
    on_line: Callable[[str], None] | None = None,
) -> ToolResult:
    """Shared runner (the git tool reuses it for real git invocations).

    ``on_line`` receives each output line (rstripped) as it arrives; when
    None, ``perm.on_output`` is used so the app layer's live echo applies
    everywhere.
    """
    timeout_s = max(1, min(int(timeout_s), _MAX_TIMEOUT_S))
    on_line = on_line or perm.on_output

    popen_args, use_shell, shell_error = _popen_spec(command, shell)
    if popen_args is None:
        return ToolResult(False, shell_error)
        
    


    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        # Own process group so Ctrl+C in our console doesn't ambiguously
        # signal the child; taskkill /T handles the whole tree regardless.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # killpg needs its own group

    try:
        proc = subprocess.Popen(
            popen_args,
            shell=use_shell,
            cwd=perm.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merged: live ordering stays sane
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
    except OSError as exc:
        return ToolResult(False, f"Could not run command: {exc}")

    lines: "queue.Queue[object]" = queue.Queue()
    thread = threading.Thread(target=_reader, args=(proc.stdout, lines), daemon=True)
    thread.start()

    chunks: list[str] = []
    collected = 0
    deadline = time.monotonic() + timeout_s
    eof = False
    try:
        while not eof:
            if time.monotonic() > deadline:
                _kill_tree(proc)
                partial = "".join(chunks).rstrip()
                tail = f"\nOutput before timeout:\n{partial}" if partial else ""
                return ToolResult(
                    False, f"Command timed out after {timeout_s}s: {command}{tail}"
                )
            try:
                item = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is _EOF:
                eof = True
                continue
            # v7.1.0: a command that prints invalid bytes must never take the
            # session down with a surrogate encode error — normalize per line.
            line = safe_text(item)
            if collected < MAX_OUTPUT_CHARS:
                chunks.append(line)
                collected += len(line)
            plain = line.rstrip("\r\n")
            if on_line is not None:
                try:
                    on_line(plain)
                except Exception:
                    pass  # a UI echo bug must never kill the command
            if perm.on_activity is not None:
                try:
                    perm.on_activity(plain)
                except Exception:
                    pass  # an event-observer bug must never kill the command
    except KeyboardInterrupt:
        # Ctrl+C cancels THIS command, not the agent turn.
        _kill_tree(proc)
        partial = "".join(chunks).rstrip()
        tail = f"\nOutput before cancel:\n{partial}" if partial else ""
        return ToolResult(False, f"Command cancelled by user (Ctrl+C): {command}{tail}")

    # The reader hit EOF, which normally means the child exited. A child that
    # closed its stdout but kept running (a detached helper, a stalled
    # daemon) must not block this thread indefinitely — bound the wait, then
    # reap the tree. Never longer than the command's own deadline.
    remaining = max(1.0, deadline - time.monotonic())
    try:
        proc.wait(timeout=min(_EXIT_GRACE_S, remaining))
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
    body = "".join(chunks).rstrip() or "(no output)"

    # Make exit code failure explicit
    success = proc.returncode == 0
    status = "✓ Success" if success else f"✗ FAILED (exit code {proc.returncode})"

    return ToolResult(success, f"{status}\n{body}", exit_code=proc.returncode)


@register(
    "run_command",
    "Run a shell command in the workspace; output streams live to the user.",
    {
        "command": "the shell command to run",
        "timeout": "(optional) seconds before the command is killed (default 60)",
        "shell": "(optional) cmd, powershell, pwsh, bash, or auto (default: system shell)",
        "background": "(optional) true to start it and return immediately (id reported)",
    },
    mutates=True,
    types={"timeout": "integer", "background": "boolean"},
)
def _run_command(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    command = str(args["command"]).strip()
    if not command:
        return ToolResult(False, "Command is empty.")
    timeout_s = int_arg(args, "timeout", _DEFAULT_TIMEOUT_S, 1, _MAX_TIMEOUT_S)
    perm.check_execute(command)
    perm.confirm_action(CATEGORY_SHELL, command)
    shell = str(args.get("shell", ""))
    if _truthy(args.get("background")):
        return _start_background(perm, command, shell)
    result = run_command(perm, command, timeout_s, shell=shell)
    _record_result(command, result)
    return result


# --- command registry (v9.2.1) -----------------------------------------------
# Every command this session really ran is recorded with its ACTUAL output and
# exit status, so the agent (and the user) can inspect what happened instead of
# relying on a recollection of the transcript. Background commands keep the
# same record while they run, which is what makes get_command_output /
# check_exit_code / inspect_process truthful rather than decorative.
@dataclass
class CommandRecord:
    """One command this session started, with its real observed state."""

    id: int
    command: str
    output: str = ""
    exit_code: int | None = None
    running: bool = True
    started_at: float = 0.0
    finished_at: float | None = None
    background: bool = False


_COMMAND_HISTORY: "deque[CommandRecord]" = deque(maxlen=20)
_COMMAND_LOCK = threading.Lock()
_COMMAND_IDS = itertools.count(1)


def _truthy(value: Any) -> bool:
    """Whether a model-supplied flag means yes (accepts bools and strings)."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _new_record(command: str, *, background: bool = False) -> CommandRecord:
    record = CommandRecord(
        id=next(_COMMAND_IDS),
        command=command,
        started_at=time.time(),
        background=background,
    )
    with _COMMAND_LOCK:
        _COMMAND_HISTORY.append(record)
    return record


def _record_result(command: str, result: ToolResult) -> CommandRecord:
    """Record a finished foreground command with its real result."""
    record = _new_record(command)
    with _COMMAND_LOCK:
        record.output = result.output
        record.exit_code = result.exit_code
        record.running = False
        record.finished_at = time.time()
    return record


def _find_record(command_id: int | None) -> CommandRecord | None:
    """The requested record, or the latest one when no id is given."""
    with _COMMAND_LOCK:
        if command_id:
            for record in _COMMAND_HISTORY:
                if record.id == command_id:
                    return record
            return None
        return _COMMAND_HISTORY[-1] if _COMMAND_HISTORY else None


def _id_arg(args: dict[str, Any]) -> int:
    """The optional command id (0 = latest, non-numeric = 0, never raises)."""
    try:
        return max(0, int(args.get("id") or 0))
    except (TypeError, ValueError):
        return 0


def _start_background(
    perm: "PermissionManager", command: str, shell: str
) -> ToolResult:
    """Start a real background process and track it in the command registry."""
    popen_args, use_shell, error = _popen_spec(command, shell)
    if popen_args is None:
        return ToolResult(False, error)
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            popen_args,
            shell=use_shell,
            cwd=perm.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
    except OSError as exc:
        return ToolResult(False, f"Could not start command: {exc}")

    record = _new_record(command, background=True)

    def pump() -> None:
        """Collect output while the process runs, then stamp the real exit."""
        try:
            for line in proc.stdout or ():
                text = safe_text(line)
                with _COMMAND_LOCK:
                    if len(record.output) < MAX_OUTPUT_CHARS:
                        record.output += text
                plain = text.rstrip("\r\n")
                for callback in (perm.on_output, perm.on_activity):
                    if callback is None:
                        continue
                    try:
                        callback(plain)
                    except Exception:
                        pass  # a UI echo bug must never kill the process
        except (OSError, ValueError):
            pass
        try:
            code: int | None = proc.wait(timeout=_EXIT_GRACE_S)
        except subprocess.TimeoutExpired:  # still running: keep that truth
            code = None
        with _COMMAND_LOCK:
            record.exit_code = code
            record.running = False
            record.finished_at = time.time()

    threading.Thread(target=pump, daemon=True).start()
    return ToolResult(
        True,
        f"Started command {record.id} in the background: {command}\n"
        f"Use get_command_output(id={record.id}) or check_exit_code(id={record.id}) "
        "to inspect it, and inspect_process to list every command.",
    )


def _detect_test_command(workspace: Any) -> str:
    """A real test command for the workspace, or "" when none is detectable."""
    root = Path(workspace)
    python_markers = ("pytest.ini", "conftest.py", "tox.ini")
    if (root / "tests").is_dir() or any((root / name).is_file() for name in python_markers):
        return "python -m pytest -q"
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "pytest" in text:
            return "python -m pytest -q"
    if (root / "package.json").is_file():
        return "npm test"
    if (root / "Cargo.toml").is_file():
        return "cargo test"
    if (root / "go.mod").is_file():
        return "go test ./..."
    return ""


@register(
    "run_tests",
    "Run the project's real test suite in the workspace and return its actual "
    "output and exit code. When no command is given, a runner is detected from "
    "the project (pytest / npm / cargo / go).",
    {
        "command": "(optional) exact test command; auto-detected when omitted",
        "timeout": "(optional) seconds before the run is killed (default 300)",
    },
    mutates=True,
    types={"timeout": "integer"},
)
def _run_tests(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    command = str(args.get("command") or "").strip()
    if not command:
        command = _detect_test_command(perm.workspace)
        if not command:
            return ToolResult(
                False,
                "Could not detect a test runner in "
                f"{perm.workspace}. Pass command= with the exact test command.",
            )
    timeout_s = int_arg(args, "timeout", _MAX_TIMEOUT_S, 1, _MAX_TIMEOUT_S)
    perm.check_execute(command)
    perm.confirm_action(CATEGORY_SHELL, command)
    result = run_command(perm, command, timeout_s, shell="")
    _record_result(command, result)
    return result


@register(
    "get_command_output",
    "Return the real captured output of a command this session ran — the latest "
    "command, or the one named by id. Never invents output.",
    {"id": "(optional) command id reported by inspect_process"},
    mutates=False,
    types={"id": "integer"},
)
def _get_command_output(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    record = _find_record(_id_arg(args))
    if record is None:
        return ToolResult(False, "No command has been run in this session yet.")
    state = "running" if record.running else f"exit code {record.exit_code}"
    body = record.output.strip() or "(no output yet)"
    return ToolResult(
        True, f"command {record.id}: {record.command} [{state}]\n{body}"
    )


@register(
    "check_exit_code",
    "Report the real exit code of a command this session ran. A command that "
    "is still running (or never exited) has no exit code — that is reported "
    "honestly rather than guessed.",
    {"id": "(optional) command id reported by inspect_process"},
    mutates=False,
    types={"id": "integer"},
)
def _check_exit_code(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    record = _find_record(_id_arg(args))
    if record is None:
        return ToolResult(True, "No command has been run in this session yet.")
    if record.running:
        return ToolResult(
            True, f"command {record.id} is still running; it has no exit code yet."
        )
    if record.exit_code is None:
        return ToolResult(
            False,
            f"command {record.id} produced no exit code (it timed out, was "
            "cancelled, or is still running).",
        )
    verdict = "success" if record.exit_code == 0 else "failure"
    return ToolResult(
        True, f"command {record.id} exited with code {record.exit_code} ({verdict})."
    )


@register(
    "inspect_process",
    "List the commands this session started with their real state: running or "
    "finished, exit code, age and command line.",
    {"id": "(optional) command id for a single detailed entry"},
    mutates=False,
    types={"id": "integer"},
)
def _inspect_process(perm: "PermissionManager", args: dict[str, Any]) -> ToolResult:
    command_id = _id_arg(args)
    with _COMMAND_LOCK:
        records = list(_COMMAND_HISTORY)
    if not records:
        return ToolResult(True, "No command has been started in this session yet.")
    if command_id:
        record = _find_record(command_id)
        if record is None:
            return ToolResult(False, f"No command with id {command_id}.")
        records = [record]
    lines = []
    for record in records:
        age = (record.finished_at or time.time()) - record.started_at
        state = "running" if record.running else f"exit {record.exit_code}"
        kind = "background" if record.background else "foreground"
        lines.append(
            f"#{record.id} [{state}] {kind} {age:.1f}s — {record.command}"
        )
    return ToolResult(True, "\n".join(lines))
