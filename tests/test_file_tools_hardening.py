"""Files-tools hardening (v9.3.2): guaranteed real workspace execution.

Covers the spec's test matrix for the existing file tools plus execution
enforcement: binary guards, atomic writes, `replace_all` edits, search flags,
`.gitignore` honoring, and — the most important part — end-to-end proof that a
user request turns into REAL file operations through the actual tool registry
and permission gate (nothing here mocks the tools).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from seedcode import codemode_state as cms
from seedcode.core.agent import AgentEngine
from seedcode.core.models import AppConfig
from seedcode.core.session import CodeSession, SessionStatus, _task_allows_read_only
from seedcode.core.tasks import Task, TaskState, TurnEvidence, verify_task
from seedcode.tools.search import _find_files, _search_text
from seedcode.tools.patch import _edit_file
from seedcode.tools.filesystem import _delete_file, _read_file, _write_file
from seedcode.tools import PermissionError_, PermissionLevel, PermissionManager
from seedcode.tools.filesystem import _delete_file, _read_file, _write_file
from seedcode.tools.patch import _edit_file
from seedcode.tools.search import _find_files, _search_text


def _perm(workspace: Path, level: PermissionLevel = PermissionLevel.WORKSPACE):
    return PermissionManager(workspace=workspace, level=level)


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
def test_read_refuses_a_probable_binary_file(tmp_path: Path) -> None:
    (tmp_path / "asset.png").write_bytes(b"\x89PNG\r\n\x00\x00binary data")
    result = _read_file(_perm(tmp_path), {"path": "asset.png"})
    assert not result.ok
    assert "binary" in result.output
    assert "Refusing" in result.output


def test_read_missing_file_gives_a_useful_error(tmp_path: Path) -> None:
    result = _read_file(_perm(tmp_path), {"path": "nope.py"})
    assert not result.ok
    assert "not found" in result.output.lower()


def test_read_an_empty_file_succeeds(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    result = _read_file(_perm(tmp_path), {"path": "empty.txt"})
    assert result.ok
    assert "0 lines" in result.output


def test_read_a_oversized_file_requires_a_range(tmp_path: Path) -> None:
    (tmp_path / "big.log").write_text("x" * (256 * 1024 + 10), encoding="utf-8")
    blunt = _read_file(_perm(tmp_path), {"path": "big.log"})
    assert not blunt.ok and "range" in blunt.output.lower()

    ranged = _read_file(_perm(tmp_path), {"path": "big.log", "start_line": 1, "end_line": 2})
    assert ranged.ok and "showing 1-" in ranged.output


def test_read_line_range_keeps_line_numbers(tmp_path: Path) -> None:
    (tmp_path / "five.py").write_text(
        "one\ntwo\nthree\nfour\nfive\n", encoding="utf-8"
    )
    result = _read_file(_perm(tmp_path), {"path": "five.py", "start_line": 2, "end_line": 3})
    assert result.ok
    assert "2\ttwo" in result.output and "3\tthree" in result.output
    # Excluded lines are not shown (the header contains the FILENAME, so the
    # check must be on numbered body lines, not on "five").
    assert "1\tone" not in result.output and "4\tfour" not in result.output


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------
def test_write_creates_a_new_file_with_parents_and_verifies(tmp_path: Path) -> None:
    result = _write_file(
        _perm(tmp_path),
        {"path": "src/app/calculator.py", "content": "print(1 + 1)\n"},
    )
    assert result.ok, result.output
    assert "verified" in result.output
    target = tmp_path / "src" / "app" / "calculator.py"
    assert target.is_file() and target.read_text(encoding="utf-8") == "print(1 + 1)\n"


def test_write_overwrites_an_existing_text_file(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("old\n", encoding="utf-8")
    result = _write_file(_perm(tmp_path), {"path": "note.txt", "content": "new\n"})
    assert result.ok
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "new\n"


def test_write_refuses_to_overwrite_a_binary_file(tmp_path: Path) -> None:
    original = b"\x00\x01\x02BINARY\x00"
    (tmp_path / "blob.bin").write_bytes(original)
    result = _write_file(_perm(tmp_path), {"path": "blob.bin", "content": "text plague"})
    assert not result.ok
    assert "corrupt" in result.output or "binary" in result.output
    assert (tmp_path / "blob.bin").read_bytes() == original  # untouched


def test_write_is_atomic_no_partial_file_on_write_error(
    tmp_path: Path, monkeypatch
) -> None:
    def broken_replace(source, destination):  # simulate a locked target (Windows)
        raise OSError("target locked")

    monkeypatch.setattr("os.replace", broken_replace)
    result = _write_file(_perm(tmp_path), {"path": "a.txt", "content": "payload\n"})
    assert not result.ok
    assert "locked" in result.output
    # The temp file is cleaned up and the target is untouched or empty-free.
    leftovers = [p.name for p in tmp_path.iterdir() if "seedcode-tmp" in p.name]
    assert leftovers == []
    assert not (tmp_path / "a.txt").exists()  # no corrupted partial file


def test_write_is_blocked_at_read_only_level(tmp_path: Path) -> None:
    with pytest.raises(PermissionError_):
        _write_file(_perm(tmp_path, PermissionLevel.READ_ONLY), {"path": "x.txt", "content": "y"})


def test_write_outside_the_workspace_is_blocked(tmp_path: Path) -> None:
    with pytest.raises(PermissionError_):
        _write_file(_perm(tmp_path), {"path": "../escape.txt", "content": "x"})


def test_delete_removes_a_file_and_verifies(tmp_path: Path) -> None:
    (tmp_path / "gone.txt").write_text("bye\n", encoding="utf-8")
    result = _delete_file(_perm(tmp_path), {"path": "gone.txt"})
    assert result.ok and "verified" in result.output
    assert not (tmp_path / "gone.txt").exists()


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------
def test_edit_replaces_exactly_once_and_preserves_unrelated_content(
    tmp_path: Path,
) -> None:
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    result = _edit_file(
        _perm(tmp_path),
        {"path": "calc.py", "old_text": "def add(a, b):", "new_text": "def add(x, y):"},
    )
    assert result.ok, result.output
    body = (tmp_path / "calc.py").read_text(encoding="utf-8")
    assert "def add(x, y):" in body
    assert "def sub(a, b):\n    return a - b" in body  # untouched
    assert "def add(a, b):" not in body


def test_edit_ambiguous_target_fails_without_guessing(tmp_path: Path) -> None:
    (tmp_path / "dup.py").write_text("same\nsame\n", encoding="utf-8")
    result = _edit_file(
        _perm(tmp_path), {"path": "dup.py", "old_text": "same", "new_text": "other"}
    )
    assert not result.ok
    assert "2 times" in result.output
    assert (tmp_path / "dup.py").read_text(encoding="utf-8") == "same\nsame\n"


def test_edit_replace_all_replaces_every_occurrence(tmp_path: Path) -> None:
    (tmp_path / "dup.py").write_text("same\nsame\nsame\n", encoding="utf-8")
    result = _edit_file(
        _perm(tmp_path),
        {"path": "dup.py", "old_text": "same", "new_text": "other", "replace_all": True},
    )
    assert result.ok and "3 replacements" in result.output
    assert (tmp_path / "dup.py").read_text(encoding="utf-8") == "other\nother\nother\n"


def test_edit_missing_original_text_fails_clearly(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha\n", encoding="utf-8")
    result = _edit_file(_perm(tmp_path), {"path": "a.txt", "old_text": "beta", "new_text": "x"})
    assert not result.ok and "was not found" in result.output


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
@pytest.fixture()
def search_ws(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("value = UPPER\nvalue = lower\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("value = other\n", encoding="utf-8")
    (tmp_path / "src" / "gen").mkdir()
    (tmp_path / "src" / "gen" / "x.py").write_text("value = generated\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("## Notes about value\n", encoding="utf-8")
    return tmp_path


def test_search_exact_case_by_default(search_ws: Path) -> None:
    expensive = _search_text(
        _perm(search_ws),
        {"pattern": "UPPER"},
    ).output
    assert "src/a.py:1" in expensive
    nothing = _search_text(_perm(search_ws), {"pattern": "nonexistentword"})
    assert nothing.ok
    assert "No matches" in nothing.output
    no_case = _search_text(_perm(search_ws), {"pattern": "LOWER"})
    assert "No matches" in no_case.output
    caseless = _search_text(
        _perm(search_ws), {"pattern": "LOWER", "ignore_case": True}
    )
    assert "src/a.py:2" in caseless.output


def test_search_ignore_case_flag(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("MixedWording = 1\n", encoding="utf-8")
    strict = _search_text(_perm(tmp_path), {"pattern": "mixedwording"})
    assert "No matches" in strict.output
    loose = _search_text(_perm(tmp_path), {"pattern": "mixedwording", "ignore_case": True})
    assert "a.py:1" in loose.output


def test_search_exclude_and_path_globs(search_ws: Path) -> None:
    expensive = _search_text(_perm(search_ws), {"pattern": "value", "exclude": "*.md"})
    assert "notes.md" not in expensive.output
    assert "src/a.py:1" in expensive.output
    # Path glob 'src/*.py' is directory-scoped: exactly one level (nested
    # files need 'src/**/*.py'). This is v9.3.2 regression coverage for the
    # fnmatch separator bug that matched 'src/gen/x.py' against 'src/*.py'.
    narrowed = _search_text(_perm(search_ws), {"pattern": "value", "glob": "src/*.py"})
    assert "src/a.py:1" in narrowed.output
    assert "src/b.py:1" in narrowed.output
    assert "src/gen/x.py" not in narrowed.output


def test_find_files_finds_by_name_and_honors_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("generated/\n*.tmp\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "skip.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "scratch.tmp").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

    found = _find_files(_perm(tmp_path), {"pattern": "*.py"}).output
    assert "main.py" in found
    assert "generated/skip.py" not in found
    assert "scratch.tmp" not in found
    assert _find_files(_perm(tmp_path), {"pattern": "*.tmp"}).output.startswith("No files")


# ---------------------------------------------------------------------------
# execution enforcement
# ---------------------------------------------------------------------------
def _read_only_evidence() -> TurnEvidence:
    return TurnEvidence(inspected=["src/base.py", "search_text: auth"], tools=3)


def test_a_read_only_task_verifies_with_real_inspection_evidence() -> None:
    task = Task(id=1, title="Review the existing auth code")
    outcome = verify_task(task, _read_only_evidence(), None, read_only=True)
    assert outcome.ok, outcome.unmet


def test_a_read_only_claim_without_inspection_never_verifies() -> None:
    task = Task(id=1, title="Review the existing auth code")
    outcome = verify_task(task, TurnEvidence(), None, read_only=True)
    assert not outcome.ok


def test_viewing_the_project_does_not_verify_a_file_modification_task(
    tmp_path: Path,
) -> None:
    task = Task(id=1, title="Add password hashing", acceptance=["file: app.py"])
    # Exactly as the session does: CLASSIFY the task's intent first, then pass
    # the read-only relaxation (never force the flag for a task whose title
    # says it must modify files).
    outcome = verify_task(
        task,
        TurnEvidence(inspected=["app.py"], tools=1),
        tmp_path,
        read_only=_task_allows_read_only(task),
    )
    assert not outcome.ok
    assert "file app.py does not exist" in "\n".join(outcome.unmet)


def test_intent_classifier_is_conservative() -> None:
    assert _task_allows_read_only(Task(id=1, title="Analyze the project structure"))
    assert _task_allows_read_only(Task(id=1, title="Explain how the TUI is built"))
    assert not _task_allows_read_only(Task(id=1, title="Add validation logic"))
    assert not _task_allows_read_only(Task(id=1, title="Review then fix the bug"))
    assert not _task_allows_read_only(Task(id=1, title="Update the README"))
    assert not _task_allows_read_only(Task(id=1, title="chat about python"))


# ---------------------------------------------------------------------------
# end-to-end: real tools, real files, real verification
# ---------------------------------------------------------------------------
@pytest.fixture()
def _codemode_state():
    cms.reset()
    yield
    cms.reset()


def _make_engine(
    workspace: Path, replies: list[str], events: list[tuple[str, str]]
) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)
    engine = AgentEngine(config, perm, on_event=lambda k, d: events.append((k, d)))
    engine._native = False  # force the text protocol (no provider calls)
    queue = iter(replies)

    def _reply():
        try:
            return iter([next(queue)])
        except StopIteration:
            return iter([""])

    engine.stream_reply = _reply  # type: ignore[method-assign]
    return engine


def _plan(tasks: list[dict]) -> str:
    return "```plan\n" + json.dumps({"tasks": tasks}) + "\n```"


def test_hello_py_created_through_the_real_write_tool(
    tmp_path: Path, monkeypatch, _codemode_state
) -> None:
    replies = [
        _plan(
            [
                {
                    "title": "Create hello.py",
                    "acceptance": ["file: hello.py | contains: Hello World"],
                }
            ]
        ),
        "```tool\n"
        + json.dumps(
            {
                "tool": "write_file",
                "args": {"path": "hello.py", "content": 'print("Hello World")\n'},
            }
        )
        + "\n```",
        "TASK 1 COMPLETE",
        "FINAL VERIFICATION PASSED",
    ]
    events: list[tuple[str, str]] = []
    engine = _make_engine(tmp_path, replies, events)
    session = CodeSession(
        engine,
        workspace=tmp_path,
        request="Create a Python file named hello.py that prints Hello World.",
        store=None,
        max_model_calls=12,
        sleep=lambda _s: None,
    )

    assert session.run() is SessionStatus.COMPLETED, session.reason

    # The file REALLY exists with the REAL content, written by the real tool.
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == 'print("Hello World")\n'
    # The TODO completed with real execution evidence, not a claim.
    task = session.graph.tasks[0]
    assert task.state is TaskState.COMPLETED
    assert "hello.py" in task.files_affected
    assert "verified" in (task.verification or "")
    # The activity stream carries the real file operation.
    assert any("hello.py" in detail for kind, detail in events)


def test_existing_file_modified_through_the_real_edit_tool(
    tmp_path: Path, monkeypatch, _codemode_state
) -> None:
    (tmp_path / "greet.py").write_text(
        'greeting = "Hello"\nprint(greeting)\n', encoding="utf-8"
    )
    replies = [
        _plan(
            [
                {
                    "title": "Personalize the greeting",
                    "acceptance": ["file: greet.py | contains: Howdy"],
                }
            ]
        ),
        "```tool\n" + json.dumps(
            {"tool": "read_file", "args": {"path": "greet.py"}}
        ) + "\n```",
        "```tool\n" + json.dumps(
            {
                "tool": "edit_file",
                "args": {
                    "path": "greet.py",
                    "old_text": '"Hello"',
                    "new_text": '"Howdy"',
                },
            }
        ) + "\n```",
        "TASK 1 COMPLETE",
        "FINAL VERIFICATION PASSED",
    ]
    events: list[tuple[str, str]] = []
    engine = _make_engine(tmp_path, replies, events)
    session = CodeSession(
        engine,
        workspace=tmp_path,
        request="Change greet.py to say Howdy.",
        store=None,
        max_model_calls=12,
        sleep=lambda _s: None,
    )

    assert session.run() is SessionStatus.COMPLETED, session.reason
    body = (tmp_path / "greet.py").read_text(encoding="utf-8")
    assert '"Howdy"' in body and '"Hello"' not in body
    assert "print(greeting)" in body  # unrelated content preserved


def test_a_read_only_session_task_completes_without_writes(
    tmp_path: Path, monkeypatch, _codemode_state
) -> None:
    (tmp_path / "notes.txt").write_text("the project is small\n", encoding="utf-8")
    replies = [
        _plan([{"title": "Inspect and summarize", "acceptance": ["no-errors"]}]),
        "```tool\n" + json.dumps({"tool": "read_file", "args": {"path": "notes.txt"}})
        + "\n```",
        "TASK 1 COMPLETE",
        "FINAL VERIFICATION PASSED",
    ]
    engine = _make_engine(tmp_path, replies, [])
    session = CodeSession(
        engine,
        workspace=tmp_path,
        request="Analyze the project.",
        store=None,
        max_model_calls=12,
        sleep=lambda _s: None,
    )

    assert session.run() is SessionStatus.COMPLETED, session.reason
    task = session.graph.tasks[0]
    assert task.state is TaskState.COMPLETED
    assert task.files_affected == []  # no write faked
    assert "notes.txt" in " ".join(task.inspected)


def test_claimed_code_with_no_file_operation_never_completes(
    tmp_path: Path, monkeypatch, _codemode_state
) -> None:
    replies = [
        _plan([{"title": "Create calculator.py", "acceptance": ["file: calculator.py"]}]),
        "I implemented the full calculator in calculator.py and it works perfectly.",
    ]
    engine = _make_engine(tmp_path, replies, [])
    session = CodeSession(
        engine,
        workspace=tmp_path,
        request="Create a Python calculator.",
        store=None,
        max_task_attempts=1,
        max_model_calls=6,
        sleep=lambda _s: None,
    )

    status = session.run()
    assert status is SessionStatus.FAILED, session.reason
    assert not (tmp_path / "calculator.py").exists()  # the claim created nothing
    assert session.graph.tasks[0].state is TaskState.FAILED
    # Grounded in the live failed run: the reason is the real unmet criterion
    # ("file calculator.py does not exist"), not a claim-based success.
    assert "file calculator.py does not exist" in (session.reason or "")
