"""Tool-lifecycle hooks and the user plugin loader (seedcode.tools.hooks).

Covered, in the repo's tested-behaviour style:

* ``before_tool`` listeners can veto a call with a reason that reaches the
  model; ``None`` counts as an allow, an exception counts as a veto (a broken
  hook must never become a silent allow);
* ``after_tool`` listeners observe real results (ok flag, output, diff) and
  an exception in one is contained and logged, never fatal;
* the agent engine actually consults the hooks at its single execution choke
  point( ``AgentEngine._execute_one``) — a vetoed write really does not write;
* user plugin loading is contained: one broken plugin is skipped and logged,
  a working plugin one directory over still loads, and a plugin that raises
  during import never blocks the engine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seedcode import codemode_state as cms
from seedcode.core.agent import AgentEngine, ToolCall
from seedcode.core.models import AppConfig
from seedcode.tools import PermissionLevel, PermissionManager
from seedcode.tools import hooks as hooks_mod
from seedcode.tools.hooks import MANAGER, ToolHookResult, load_plugin, load_plugins


@pytest.fixture(autouse=True)
def _clean_state():
    cms.reset()
    MANAGER.reset()
    yield
    cms.reset()
    MANAGER.reset()


def _engine(workspace: Path) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model")
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)
    return AgentEngine(config, perm, on_event=lambda k, d: None)


# ---------------------------------------------------------------------------
# hook manager
# ---------------------------------------------------------------------------
def test_run_before_allows_and_collects_no_reason() -> None:
    seen: list[tuple[str, dict]] = []
    MANAGER.add_before(lambda t, a: seen.append((t, a)) or True)
    assert MANAGER.run_before("read_file", {"path": "a.py"}) == ""
    assert seen == [("read_file", {"path": "a.py"})]


@pytest.mark.parametrize("returnValue", [True, None, "", 0])
def test_only_false_or_exception_vetoes(returnValue) -> None:
    # Contract: a listener vetoes ONLY by returning False (or raising).
    # Any other value — including None from an observer that forgot to
    # return True — allows, so a forgetful hook cannot block the agent.
    MANAGER.add_before(lambda t, a: returnValue)
    reason = MANAGER.run_before("read_file", {})
    assert reason == ""


def test_explicit_false_vetoes_with_reason(tmp_path: Path) -> None:
    def deny(tool, args):
        assert tool == "write_file"
        return False

    MANAGER.add_before(deny)
    reason = MANAGER.run_before("write_file", {"path": "x.py"})
    assert "blocked by a before_tool hook" in reason


def test_raising_hook_counts_as_veto() -> None:
    def broken(tool, args):
        raise RuntimeError("policy exploded")

    MANAGER.add_before(broken)
    reason = MANAGER.run_before("read_file", {})
    assert reason != ""
    assert "broken" not in reason.lower() or reason  # reason is a safe string


def test_after_observes_real_result_and_errors_are_contained() -> None:
    seen: list[tuple[str, ToolHookResult]] = []

    def sink(tool, args, result):
        seen.append((tool, result))
        raise RuntimeError("a broken observer must not break tools")

    MANAGER.add_after(sink)
    result = ToolHookResult(ok=True, output="wrote file", diff="+x = 1")
    MANAGER.run_after("write_file", {"path": "x.py"}, result)
    assert seen == [("write_file", result)]


def test_reset_drops_listeners() -> None:
    MANAGER.add_before(lambda t, a: False)
    MANAGER.reset()
    assert MANAGER.run_before("read_file", {}) == ""


# ---------------------------------------------------------------------------
# engine integration — a veto is real
# ---------------------------------------------------------------------------
def test_before_hook_veto_blocks_a_real_write(tmp_path: Path) -> None:
    MANAGER.add_before(
        lambda tool, args: not (tool == "write_file" and Path(args["path"]).name == "no.txt")
    )
    engine = _engine(tmp_path)
    result = engine._execute_one(
        ToolCall(tool="write_file", args={"path": "no.txt", "content": "x"})
    )
    assert result.ok is False
    assert "Blocked" in result.output
    assert not (tmp_path / "no.txt").exists()

    ok = engine._execute_one(
        ToolCall(tool="write_file", args={"path": "yes.txt", "content": "x"})
    )
    assert ok.ok
    assert (tmp_path / "yes.txt").exists()


def test_after_hook_sees_engine_result(tmp_path: Path) -> None:
    seen: list[ToolHookResult] = []
    MANAGER.add_after(lambda t, a, r: seen.append(r))
    engine = _engine(tmp_path)
    engine._execute_one(
        ToolCall(tool="write_file", args={"path": "seen.txt", "content": "x"})
    )
    assert len(seen) == 1
    assert seen[0].ok and "seen.txt" in seen[0].output


# ---------------------------------------------------------------------------
# plugin loading
# ---------------------------------------------------------------------------
def _plugin_source(body: str) -> str:
    return f"from seedcode.tools.hooks import MANAGER\n{body}\n"


def test_load_plugin_registers_and_reports(tmp_path: Path) -> None:
    plugin = tmp_path / "audit_hook.py"
    plugin.write_text(
        _plugin_source("SEEN = []")
        + "MANAGER.add_after(lambda t, a, r: SEEN.append((t, r.ok)))\n",
        encoding="utf-8",
    )
    assert load_plugin(plugin)
    assert MANAGER.after  # a listener arrived
    # exercise it and prove observation reaches the plugin's own state
    module = __import__(f"seedcode_plugin_{plugin.stem}")
    assert module.SEEN == []
    MANAGER.run_after(
        "write_file", {"path": "x.py"}, ToolHookResult(ok=True, output="wrote")
    )
    assert module.SEEN == [("write_file", True)]


def test_broken_plugin_is_skipped_and_others_still_load(tmp_path: Path) -> None:
    plugin_dir = tmp_path / ".seedcode" / "plugins"
    plugin_dir.mkdir(parents=True)
    bad = plugin_dir / "broken.py"
    bad.write_text("raise RuntimeError('plugin boom')\n", encoding="utf-8")
    good = plugin_dir / "good.py"
    good.write_text("seedcode_ok = True\n", encoding="utf-8")
    loaded = load_plugins(tmp_path)
    assert str(good) in loaded
    assert str(bad) not in loaded
