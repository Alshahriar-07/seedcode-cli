"""v9.2.1 tool surface, cloud reasoning and version consistency.

These tests execute the REAL tools — real files on disk, real
``subprocess`` commands, real exit codes — because the whole point of the
v9.2.1 repair is that the agent's tool calls must have observable effects
rather than being described in prose. Only the provider wire format is faked
(for the reasoning-channel parser), which is exactly the layer a test cannot
reach without a live server.

Covered:

* ``create_file`` / ``copy_file`` really create files, verify their contents,
  and refuse to clobber an existing file by accident;
* the contract aliases (``list_directory`` / ``search_files`` /
  ``search_content``) share the implementation of the tool they name;
* ``run_command`` records its real exit code and output, and
  ``get_command_output`` / ``check_exit_code`` / ``inspect_process`` report
  that observed state;
* ``run_tests`` detects a runner and returns the real suite result, both for a
  passing and a failing suite;
* a background command is tracked while it runs and keeps its real output;
* OpenAI-compatible reasoning deltas become ``ThinkingDelta`` events (never
  answer text), and a model without the channel produces none;
* the version is identical in every consumer (package, npm, installer, CLI).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from seedcode import __version__
from seedcode.core.providers.base import TextDelta, ThinkingDelta
from seedcode.core.providers.openrouter import _iter_tool_stream
from seedcode.tools import (
    TOOL_REGISTRY,
    PermissionLevel,
    PermissionManager,
    get_tool,
)

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _perm(workspace: Path) -> PermissionManager:
    return PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)


def _run(workspace: Path, name: str, **args):
    """Execute a registered tool exactly as the agent does."""
    return get_tool(name).run(_perm(workspace), args)


def _python(code: str) -> str:
    """A portable one-liner using THIS interpreter (single quotes only)."""
    exe = sys.executable
    if " " in exe:
        exe = f'"{exe}"'
    return f"{exe} -c \"{code}\""


# ---------------------------------------------------------------------------
# file tools: real effects, verified
# ---------------------------------------------------------------------------
def test_create_file_writes_real_content_and_refuses_to_clobber(tmp_path: Path) -> None:
    result = _run(tmp_path, "create_file", path="pkg/module.py", content="VALUE = 42\n")
    assert result.ok, result.output
    target = tmp_path / "pkg" / "module.py"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "VALUE = 42\n"

    # A second create must not silently overwrite the user's file.
    again = _run(tmp_path, "create_file", path="pkg/module.py", content="OTHER = 1\n")
    assert not again.ok
    assert "already exists" in again.output
    assert target.read_text(encoding="utf-8") == "VALUE = 42\n"


def test_copy_file_copies_bytes_and_verifies(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload\n", encoding="utf-8")

    result = _run(tmp_path, "copy_file", source="source.txt", destination="nested/copy.txt")
    assert result.ok, result.output
    assert (tmp_path / "nested" / "copy.txt").read_bytes() == source.read_bytes()

    # Existing destination: refuse unless overwrite is explicit.
    blocked = _run(tmp_path, "copy_file", source="source.txt", destination="nested/copy.txt")
    assert not blocked.ok and "already exists" in blocked.output
    overwritten = _run(
        tmp_path,
        "copy_file",
        source="source.txt",
        destination="nested/copy.txt",
        overwrite=True,
    )
    assert overwritten.ok, overwritten.output


def test_contract_aliases_reuse_the_real_implementation(tmp_path: Path) -> None:
    for alias, target in (
        ("list_directory", "list_dir"),
        ("search_files", "find_files"),
        ("search_content", "search_text"),
    ):
        assert alias in TOOL_REGISTRY
        assert TOOL_REGISTRY[alias].runner is TOOL_REGISTRY[target].runner
        assert set(TOOL_REGISTRY[alias].args) == set(TOOL_REGISTRY[target].args)

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    listing = _run(tmp_path, "list_directory", path=".")
    assert listing.ok and "src" in listing.output
    found = _run(tmp_path, "search_files", pattern="*.py")
    assert found.ok and "app.py" in found.output


# ---------------------------------------------------------------------------
# terminal tools: real exit codes and captured output
# ---------------------------------------------------------------------------
def test_run_command_records_real_exit_code_and_output(tmp_path: Path) -> None:
    result = _run(
        tmp_path, "run_command", command=_python("print('hello-v921'); raise SystemExit(3)")
    )
    assert not result.ok
    assert result.exit_code == 3

    code = _run(tmp_path, "check_exit_code")
    assert code.ok and "code 3" in code.output

    output = _run(tmp_path, "get_command_output")
    assert output.ok and "hello-v921" in output.output


def test_inspect_process_lists_the_session_commands(tmp_path: Path) -> None:
    _run(tmp_path, "run_command", command=_python("print('listed')"))
    listing = _run(tmp_path, "inspect_process")
    assert listing.ok
    assert "foreground" in listing.output and "print('listed')" in listing.output


def test_background_command_is_tracked_until_it_exits(tmp_path: Path) -> None:
    started = _run(tmp_path, "run_command", command=_python("print('bg-ok')"), background=True)
    assert started.ok, started.output
    command_id = int(started.output.split("command ", 1)[1].split(" ", 1)[0])

    deadline = time.time() + 15
    while time.time() < deadline:
        status = _run(tmp_path, "check_exit_code", id=command_id)
        if "exited with code" in status.output:
            break
        time.sleep(0.1)
    assert "exited with code 0" in status.output, status.output

    output = _run(tmp_path, "get_command_output", id=command_id)
    assert output.ok and "bg-ok" in output.output


def test_run_tests_detects_and_reports_a_real_passing_suite(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    result = _run(tmp_path, "run_tests")
    assert result.ok, result.output
    assert result.exit_code == 0
    assert "1 passed" in result.output


def test_run_tests_reports_a_real_failing_suite(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_bad.py").write_text(
        "def test_bad():\n    assert 2 + 2 == 5\n", encoding="utf-8"
    )
    result = _run(tmp_path, "run_tests")
    assert not result.ok
    assert result.exit_code not in (0, None)
    assert "1 failed" in result.output


# ---------------------------------------------------------------------------
# cloud reasoning channel (never fabricated)
# ---------------------------------------------------------------------------
def _chunk(**delta_fields: object) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(**delta_fields))]
    )


def test_reasoning_deltas_become_thinking_events_not_answer_text() -> None:
    events = list(
        _iter_tool_stream(
            iter(
                [
                    _chunk(reasoning="inspecting the module layout"),
                    _chunk(reasoning_content=" then patching the dispatcher"),
                    _chunk(content="Done: patched it."),
                ]
            )
        )
    )
    assert [type(event) for event in events] == [
        ThinkingDelta,
        ThinkingDelta,
        TextDelta,
    ]
    assert events[0].text == "inspecting the module layout"
    assert events[1].text == " then patching the dispatcher"
    assert events[-1].text == "Done: patched it."


def test_reasoning_details_list_is_relayed() -> None:
    events = list(
        _iter_tool_stream(
            iter([_chunk(reasoning_details=[{"text": "step one"}], content="answer")])
        )
    )
    assert [type(event) for event in events] == [ThinkingDelta, TextDelta]
    assert events[0].text == "step one"


def test_a_model_without_a_reasoning_channel_produces_no_thinking() -> None:
    events = list(_iter_tool_stream(iter([_chunk(content="plain answer")])))
    assert [type(event) for event in events] == [TextDelta]


# ---------------------------------------------------------------------------
# version consistency
# ---------------------------------------------------------------------------
def test_version_agrees_in_every_consumer() -> None:
    assert __version__ == "10.2.1"

    npm = json.loads((ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
    assert npm["version"] == __version__

    iss = (ROOT / "scripts" / "windows" / "setup.iss").read_text(encoding="utf-8")
    assert f'#define MyAppVersion "{__version__}"' in iss

    proc = subprocess.run(
        [sys.executable, "-m", "seedcode", "--version"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"Seed Code CLI {__version__}"
