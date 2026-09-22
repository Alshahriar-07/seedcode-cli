"""v7.1.0 regression: lone surrogates must never break a Code Mode session.

During v6.2.5 testing this crashed a run:

    ✖ Unexpected error: 'utf-8' codec can't encode characters in
      position 6159-6160: surrogates not allowed

A Python ``str`` can hold surrogate code units (U+D800-U+DFFF) that no UTF-8
encoder accepts — they arrive from model output, tool output, file content,
command stdout, and any serialization of those. This suite locks in the fix at
every boundary:

* model output (a reply/stream containing a lone surrogate),
* tool output (a ``ToolResult`` whose text holds one),
* file content (writing and reading back),
* terminal/stdout output (a command that emits invalid UTF-8 bytes),
* serialized tool results and session checkpoints (JSON on disk),
* the Code Mode task view and the persistent session (they must keep running).

And it proves the fix is not a blanket strip: valid Unicode — Bangla, emoji,
Chinese, Japanese, Arabic, combining marks — round-trips unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from rich.console import Console

from seedcode import codemode_state as cms
from seedcode.core.agent import AgentEngine
from seedcode.core.models import AppConfig, Message
from seedcode.core.session import CodeSession, SessionStatus, TurnEvidence
from seedcode.core.tasks import TaskGraph, parse_plan
from seedcode.tools import PermissionLevel, PermissionManager
from seedcode.tools.base import ToolResult
from seedcode.ui import UI
from seedcode.ui.tasks import TaskFlow
from seedcode.ui.theme import SEED_THEME
from seedcode.utils.text import (
    contains_surrogates,
    safe_encode,
    safe_text,
    strip_surrogates,
)

# A lone high surrogate and a lone low surrogate — each is unencodable.
LONE_HIGH = "\ud83d"
LONE_LOW = "\ude00"
# The same two code units *together* are a real emoji (U+1F600).
PAIR = "\ud83d\ude00"
EMOJI = "😀"

VALID_UNICODE = {
    "bangla": "বাংলা ভাষা",
    "emoji": "😀🎉🚀",
    "chinese": "中文字符",
    "japanese": "日本語テスト",
    "arabic": "مرحبا بالعالم",
    "combining": "e\u0301a\u0300",
    "mixed": "বাংলা 中文 日本語 العربية 😀",
}


def _console(width: int = 100) -> Console:
    import io

    return Console(
        theme=SEED_THEME,
        width=width,
        file=io.StringIO(),
        force_terminal=False,
        legacy_windows=False,
        record=True,
        highlight=False,
    )


def _ui(width: int = 100) -> UI:
    ui = UI(plain=True)
    ui.console = _console(width)
    return ui


@pytest.fixture(autouse=True)
def _clean_state():
    cms.reset()
    from seedcode.core import session as session_mod

    session_mod.reset()
    yield
    cms.reset()
    session_mod.reset()


# --- the normalization itself -------------------------------------------------


def test_lone_surrogates_are_replaced_not_raised() -> None:
    text = f"file {LONE_HIGH}name{LONE_LOW}.py"
    cleaned = strip_surrogates(text)
    assert contains_surrogates(cleaned) is False
    cleaned.encode("utf-8")  # the whole point: this must not raise


def test_split_emoji_pair_is_repaired_to_the_real_character() -> None:
    assert strip_surrogates(PAIR) == EMOJI
    assert strip_surrogates(f"hi {PAIR} there") == f"hi {EMOJI} there"


def test_lone_surrogate_becomes_the_replacement_character() -> None:
    assert strip_surrogates(f"a{LONE_HIGH}b") == "a\ufffdb"
    assert strip_surrogates(LONE_LOW) == "\ufffd"


@pytest.mark.parametrize("name,text", sorted(VALID_UNICODE.items()))
def test_valid_unicode_is_never_altered(name: str, text: str) -> None:
    assert strip_surrogates(text) == text
    assert safe_text(text) == text
    assert safe_encode(text).decode("utf-8") == text


def test_safe_helpers_never_raise_on_odd_input() -> None:
    assert safe_text(None) == "None"
    assert strip_surrogates("") == ""
    assert safe_encode(LONE_HIGH) == "\ufffd".encode("utf-8")
    class _Weird:
        def __str__(self) -> str:
            return f"weird{LONE_HIGH}"

    assert not contains_surrogates(safe_text(_Weird()))


# --- model output -------------------------------------------------------------


def test_message_content_normalises_surrogates() -> None:
    message = Message(role="assistant", content=f"Done {LONE_HIGH}")
    assert not contains_surrogates(message.content)
    assert message.content == "Done \ufffd"
    json.dumps(message.model_dump()).encode("utf-8")  # serializable


def test_message_keeps_valid_unicode() -> None:
    for text in VALID_UNICODE.values():
        assert Message(role="user", content=text).content == text


def test_message_tool_arguments_are_normalised() -> None:
    from seedcode.core.models import ToolCallRecord

    record = ToolCallRecord(
        name="write_file",
        arguments={"path": f"a{LONE_HIGH}.py", "content": f"x{LONE_LOW}"},
    )
    json.dumps(record.arguments).encode("utf-8")
    assert not contains_surrogates(record.arguments["path"])
    assert not contains_surrogates(record.arguments["content"])


class _ScriptedAgent:
    """Minimal agent double whose replies carry surrogates."""

    def __init__(self, replies: list[str]) -> None:
        self.messages = [Message(role="system", content="system")]
        self.transcript = list(self.messages)
        self.last_evidence = TurnEvidence(changed_files=["app.py"])
        self._replies = list(replies)

    def run_turn(self, text: str) -> str:
        reply = self._replies.pop(0) if self._replies else "ok"
        self.last_evidence = TurnEvidence(changed_files=["app.py"])
        return reply


def test_streamed_model_output_with_a_surrogate_renders_safely() -> None:
    """The streaming renderer must not raise on a split surrogate."""

    ui = _ui()
    with ui.streaming() as renderer:
        renderer.feed(f"partial {LONE_HIGH}")
        renderer.feed(f"{LONE_LOW} tail")
        renderer.flush()
    assert not contains_surrogates(renderer.text)
    ui.console.export_text()  # encoding the frame must not raise


def test_agent_turn_with_surrogate_reply_and_tool_args(tmp_path: Path, monkeypatch) -> None:
    """A whole agent turn: model reply + tool call + file write, all with junk."""
    monkeypatch.chdir(tmp_path)
    cms.enable(tmp_path)
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
    events: list[tuple[str, str]] = []
    engine = AgentEngine(config, perm, on_event=lambda k, d: events.append((k, d)))
    engine._native = False

    block = "```tool\n" + json.dumps(
        {
            "tool": "write_file",
            "args": {"path": "safe.py", "content": f"x = 'ok {LONE_HIGH}'"},
        }
    ) + "\n```"
    replies = iter([f"Working {LONE_HIGH}\n{block}", f"Done {LONE_LOW}"])

    def _reply():
        return iter([next(replies, "")])

    engine.stream_reply = _reply  # type: ignore[method-assign]
    final = engine.run_turn(f"write something {LONE_HIGH}")

    assert not contains_surrogates(final)
    written = (tmp_path / "safe.py").read_text(encoding="utf-8")  # must not raise
    assert "ok" in written
    # Every narrated detail is safe to print.
    for _kind, detail in events:
        assert not contains_surrogates(detail)


# --- tool output --------------------------------------------------------------


def test_tool_result_output_is_normalized_on_construction() -> None:
    result = ToolResult(True, f"stdout {LONE_HIGH} more")
    assert not contains_surrogates(result.output)
    assert not contains_surrogates(result.for_model())
    json.dumps({"output": result.for_model()}).encode("utf-8")


def test_run_command_with_invalid_utf8_bytes_stays_safe(tmp_path: Path) -> None:
    """A command that writes raw invalid bytes must not break the tool."""
    from seedcode.tools.terminal import run_command

    perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
    perm.confirm_action = lambda *a, **k: None  # no interactive prompt in tests
    perm.check_execute = lambda *a, **k: None

    command = (
        f'"{sys.executable}" -c "'
        "import sys;sys.stdout.buffer.write(bytes([255,254,10]))"
        '"'
    )
    result = run_command(perm, command, 60)
    assert not contains_surrogates(result.output)
    result.output.encode("utf-8")  # encodable for the model request


def test_serialized_tool_results_are_encodable() -> None:
    results = [ToolResult(True, f"line {LONE_HIGH}"), ToolResult(False, f"{LONE_LOW}")]
    payload = json.dumps([r.for_model() for r in results], ensure_ascii=False)
    payload.encode("utf-8")


# --- file content -------------------------------------------------------------


def test_write_file_repairs_surrogates_and_reports_it(tmp_path: Path) -> None:
    from seedcode.tools.filesystem import _write_file

    perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
    result = _write_file(
        perm, {"path": "notes.txt", "content": f"hello {LONE_HIGH} world"}
    )
    assert result.ok, result.output
    assert "U+FFFD" in result.output  # honest about the repair
    body = (tmp_path / "notes.txt").read_text(encoding="utf-8")  # must not raise
    assert "hello" in body and "world" in body
    assert not contains_surrogates(body)


def test_write_and_read_file_keep_valid_unicode(tmp_path: Path) -> None:
    from seedcode.tools.filesystem import _read_file, _write_file

    perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
    for name, text in VALID_UNICODE.items():
        path = f"{name}.txt"
        assert _write_file(perm, {"path": path, "content": text}).ok
        read = _read_file(perm, {"path": path})
        assert read.ok
        assert text in read.output


def test_reading_a_file_with_invalid_bytes_never_raises(tmp_path: Path) -> None:
    from seedcode.tools.filesystem import _read_file

    (tmp_path / "binary.txt").write_bytes(b"start \xff\xfe end")
    perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
    read = _read_file(perm, {"path": "binary.txt"})
    assert not contains_surrogates(read.output)
    read.output.encode("utf-8")


# --- session checkpoints / serialized state -----------------------------------


def test_checkpoint_with_surrogates_is_written_and_read_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    state = cms.enable(tmp_path)
    store = state.store

    payload = {
        "request": f"fix {LONE_HIGH}",
        "changed_files": [f"a{LONE_LOW}.py"],
        "errors": [f"boom {LONE_HIGH}"],
    }
    assert store.save_checkpoint(payload) is True
    loaded = store.load_checkpoint()
    assert loaded is not None
    assert not contains_surrogates(json.dumps(loaded))


def test_session_completes_when_every_reply_has_surrogates(tmp_path: Path, monkeypatch) -> None:
    """Invalid Unicode must never terminate a Code Mode session."""
    monkeypatch.chdir(tmp_path)
    cms.enable(tmp_path)
    store = cms.codemode_state().store
    replies = [
        "1. Build the thing — Acceptance: no-errors\n",
        f"created app.py {LONE_HIGH}",
        f"verified {LONE_LOW}",
    ]
    agent = _ScriptedAgent(replies)
    session = CodeSession(agent, workspace=tmp_path, request=f"build {LONE_HIGH}", store=store)

    status = session.run()

    assert status is SessionStatus.COMPLETED
    done, total = session.graph.progress()
    assert done == total == 1
    for text in session.state.changed_files + session.state.commands + session.state.errors:
        assert not contains_surrogates(text)


def test_plan_parsing_survives_surrogate_titles(tmp_path: Path) -> None:
    items = parse_plan(f"1. Fix the {LONE_HIGH} parser — Acceptance: no-errors")
    graph = TaskGraph.from_plan("req", items)
    assert graph.total == 1
    json.dumps(graph.to_dict()).encode("utf-8")


# --- the task view ------------------------------------------------------------


def test_task_view_renders_surrogates_in_titles_and_details() -> None:
    console = _console()
    flow = TaskFlow(console, mode_label="Code Mode", task=f"Fix {LONE_HIGH}").begin()
    flow.attach_plan(
        TaskGraph.from_plan("req", parse_plan(f"1. Fix {LONE_LOW} parser"))
    )
    flow.set_activity(f"Editing src/{LONE_HIGH}.ts")
    flow.observe_tool_start("read_file", {"path": f"src/{LONE_LOW}.py"})
    flow.observe_tool_done("read_file", True, f"body {LONE_HIGH}")
    flow.finish("completed")
    out = console.export_text()
    assert not contains_surrogates(out)
    assert "Task" in out


def test_console_that_cannot_encode_valid_unicode_still_renders() -> None:
    """The same failure class from the other side: a cp1252 console.

    A raster-font cmd.exe on a legacy code page cannot encode "→" or "বাংলা".
    Rich raises mid-render there; the UI must degrade instead, because a
    display error during a Code Mode session must never end it.
    """
    import io as _io

    ui = UI(plain=True)
    stream = _io.TextIOWrapper(_io.BytesIO(), encoding="cp1252", errors="strict")
    ui.console = Console(theme=SEED_THEME, width=80, file=stream)

    ui.print("→ hello ✓")
    ui.info("বাংলা ভাষা")
    ui.dim("日本語 😀")
    ui.success("done")
    ui.blank()
    ui.statusbar(AppConfig(provider="default", model="m"))
    with ui.streaming() as renderer:
        renderer.feed("→ বাংলা 😀")
        renderer.flush()
    stream.flush()  # nothing raised


def test_ui_messaging_and_print_never_raise_on_surrogates() -> None:
    ui = _ui()
    ui.info(f"info {LONE_HIGH}")
    ui.dim(f"dim {LONE_LOW}")
    ui.success(f"ok {LONE_HIGH}")
    ui.warning(f"warn {LONE_HIGH}")
    ui.error(f"err {LONE_LOW}")
    ui.print(f"plain {LONE_HIGH}")
    ui.blank()
    exported = ui.console.export_text()
    assert "info" in exported and "plain" in exported
    assert not contains_surrogates(exported)
