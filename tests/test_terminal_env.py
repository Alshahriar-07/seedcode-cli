"""Phase 4: terminal-host, shell, and environment detection.

Every case injects an explicit environment mapping, so the results never
depend on where the test suite happens to run.
"""

from __future__ import annotations

from pathlib import Path

from seedcode.utils import terminal_env as t


# --- host detection ----------------------------------------------------------

def test_vscode_detected_from_term_program() -> None:
    assert t.detect_host({"TERM_PROGRAM": "vscode"}) == t.HOST_VSCODE


def test_vscode_detected_from_pid_marker() -> None:
    assert t.detect_host({"VSCODE_PID": "1234"}) == t.HOST_VSCODE


def test_windows_terminal_detected() -> None:
    assert t.detect_host({"WT_SESSION": "abc"}) == t.HOST_WINDOWS_TERMINAL


def test_unknown_host_is_safe() -> None:
    assert t.detect_host({}) == t.HOST_UNKNOWN


def test_vscode_beats_windows_terminal() -> None:
    env = {"TERM_PROGRAM": "vscode", "WT_SESSION": "abc"}
    assert t.detect_host(env) == t.HOST_VSCODE


# --- shell detection ---------------------------------------------------------

def test_shell_powershell_on_windows() -> None:
    env = {"PSModulePath": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\Modules"}
    assert t.detect_shell(env, platform="win32") == t.SHELL_POWERSHELL


def test_shell_pwsh_on_windows() -> None:
    env = {
        "PSModulePath": "C:\\Program Files\\PowerShell\\7\\Modules",
        "POWERSHELL_DISTRIBUTION_CHANNEL": "MSI:Windows",
    }
    assert t.detect_shell(env, platform="win32") == t.SHELL_PWSH


def test_shell_cmd_on_windows() -> None:
    env = {"COMSPEC": "C:\\Windows\\system32\\cmd.exe"}
    assert t.detect_shell(env, platform="win32") == t.SHELL_CMD


def test_shell_git_bash_on_windows() -> None:
    assert t.detect_shell({"MSYSTEM": "MINGW64"}, platform="win32") == t.SHELL_BASH


def test_shell_posix_from_env_shell() -> None:
    assert t.detect_shell({"SHELL": "/bin/bash"}, platform="linux") == t.SHELL_BASH


def test_shell_override_wins() -> None:
    env = {"SEEDCODE_SHELL": "cmd", "MSYSTEM": "MINGW64"}
    assert t.detect_shell(env, platform="win32") == t.SHELL_CMD


def test_shell_unknown_is_empty() -> None:
    assert t.detect_shell({}, platform="linux") == ""


# --- run_command shell key ---------------------------------------------------

def test_run_command_shell_maps_pwsh_to_powershell() -> None:
    assert t.run_command_shell({"SHELL": "/usr/bin/pwsh"}, platform="linux") == t.SHELL_POWERSHELL


def test_run_command_shell_unknown_is_empty() -> None:
    assert t.run_command_shell({}, platform="linux") == ""


# --- terminal snapshot -------------------------------------------------------

def test_detect_terminal_plain_modes() -> None:
    assert t.detect_terminal({"TERM": "dumb"}).plain
    assert t.detect_terminal({"SEEDCODE_PLAIN": "1"}).plain
    assert not t.detect_terminal({}).plain


def test_detect_terminal_passthrough_no_raise() -> None:
    env = t.detect_terminal(
        {"TERM_PROGRAM": "vscode", "PSModulePath": "x"},
        platform="win32",
        isatty=True,
        columns=120,
    )
    assert env.is_vscode
    assert env.shell == t.SHELL_POWERSHELL
    assert env.columns == 120 and env.is_tty
    assert "vscode" in env.describe()


# --- workspace root ----------------------------------------------------------

def test_workspace_root_is_cwd() -> None:
    assert t.workspace_root() == Path.cwd()


def test_default_permission_workspace_is_cwd() -> None:
    from seedcode.tools import PermissionManager

    assert PermissionManager().workspace == Path.cwd().resolve()


# --- the terminal tool honours detected/auto shells --------------------------

def test_shell_registry_covers_pwsh_and_bash() -> None:
    from seedcode.tools.terminal import _SHELLS

    assert {"cmd", "powershell", "pwsh", "bash"} <= set(_SHELLS)


def test_run_command_auto_falls_back_when_shell_unknown(tmp_path, monkeypatch) -> None:
    from seedcode.tools import PermissionManager
    from seedcode.tools import terminal as term

    perm = PermissionManager(workspace=tmp_path)
    monkeypatch.setattr(term, "run_command_shell", lambda *a, **k: "")
    result = term.run_command(perm, "echo auto-ok", 10, shell="auto")
    assert result.ok and "auto-ok" in result.output
