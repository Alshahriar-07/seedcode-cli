"""Agent profiles (seedcode.core.agents): registry, user overrides, engine wiring.

OpenCode models selectable agents as registry entries with a persona and a
capability restriction; these tests pin the same behaviour for SeedCode:

* the built-ins resolve, default first, with ``build`` behaviour-neutral;
* unknown ids resolve to the default (never crash);
* user agents load from ``agents.json`` with real validation — a broken
  entry is skipped (never fatal) and a same-id definition overrides the
  built-in;
* enforcement is real: a read-only profile's engine refuses a mutating tool
  call, and native specs exclude tools outside the whitelist;
* ``/agents``-driven switching re-renders the system prompt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seedcode import codemode_state as cms
from seedcode.core.agent import AgentEngine, ToolCall
from seedcode.core.agents import (
    DEFAULT_AGENT_ID,
    AgentRegistry,
    _parse_agents_file,
    describe,
)
from seedcode.core.models import AppConfig
from seedcode.tools import PermissionLevel, PermissionManager
from seedcode.tools.base import tool_specs


@pytest.fixture(autouse=True)
def _clean_codemode():
    cms.reset()
    yield
    cms.reset()


def _perm(workspace: Path) -> PermissionManager:
    return PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)


def _engine(workspace: Path, agent_id: str | None = None, **kwargs) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model")
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=workspace, level=PermissionLevel.WORKSPACE)
    return AgentEngine(
        config, perm, agent_id=agent_id, on_event=lambda k, d: None, **kwargs
    )


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def test_builtins_listing_and_default_first() -> None:
    registry = AgentRegistry()
    ids = [a.id for a in registry.list()]
    assert ids[0] == DEFAULT_AGENT_ID == "build"
    assert {"plan", "explore", "general"} <= set(ids)
    for profile in registry.list():
        assert profile.system  # every persona carries a real instruction
        assert describe(profile).startswith(profile.name)


def test_unknown_agent_resolves_to_build_default() -> None:
    registry = AgentRegistry()
    assert registry.resolve("no-such-agent").id == "build"
    assert registry.resolve(None).id == "build"
    assert registry.resolve("  plan  ").id == "plan"


def test_plan_and_explore_are_read_only_whitelists() -> None:
    registry = AgentRegistry()
    for aid in ("plan", "explore"):
        profile = registry.get(aid)
        assert profile is not None
        assert profile.tools is not None
        assert "write_file" not in profile.tools
        assert "read_file" in profile.tools
        assert profile.steps is None or profile.steps >= 1


def test_override_wins_and_registry_never_mutates_builtins(tmp_path: Path) -> None:
    source = tmp_path / "agents.json"
    source.write_text(
        json.dumps(
            {"build": {"name": "Repo Build", "system": "Always run pytest first."}}
        ),
        encoding="utf-8",
    )
    profiles = _parse_agents_file(source, "project")
    assert profiles["build"].name == "Repo Build"

    from seedcode.core import agents as agents_mod

    original = agents_mod._agents_files
    agents_mod._agents_files = lambda ws: [(source, "project")]
    try:
        registry = AgentRegistry(tmp_path)
        assert registry.get("build").name == "Repo Build"
    finally:
        agents_mod._agents_files = original
    # the module-level built-in was never mutated
    assert AgentRegistry().get("build").name == "Build"


def test_broken_agents_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    bad = tmp_path / "agents.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _parse_agents_file(bad, "project") == {}

    no_system = tmp_path / "agents2.json"
    no_system.write_text(json.dumps({"x": {"name": "X"}}), encoding="utf-8")
    assert _parse_agents_file(no_system, "project") == {}

    bad_types = tmp_path / "agents3.json"
    bad_types.write_text(
        json.dumps(
            {
                "a": ["not", "an", "object"],
                "b": {"system": "s", "steps": 0},
                "c": {"system": "s", "tools": ["read_file"]},
            }
        ),
        encoding="utf-8",
    )
    parsed = _parse_agents_file(bad_types, "project")
    assert set(parsed) == {"c"}
    assert parsed["c"].tools == frozenset({"read_file"})


def test_disabled_flag_drops_an_agent(tmp_path: Path) -> None:
    source = tmp_path / "agents.json"
    source.write_text(
        json.dumps({"ghost": {"name": "Ghost", "system": "s", "disabled": True}}),
        encoding="utf-8",
    )
    assert _parse_agents_file(source, "project") == {}


# ---------------------------------------------------------------------------
# engine wiring
# ---------------------------------------------------------------------------
def test_engine_default_profile_is_neutral(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert engine.profile.id == "build"
    assert engine.profile.tools is None  # build adds no restriction


def test_readonly_engine_refuses_mutating_tool(tmp_path: Path) -> None:
    engine = _engine(tmp_path, agent_id="plan")
    assert engine.profile.id == "plan"
    assert engine.max_steps == 10  # the plan profile's budget


def test_write_file_blocked_for_plan_real_call(tmp_path: Path) -> None:
    engine = _engine(tmp_path, agent_id="plan")
    result = engine._execute_one(
        ToolCall(tool="write_file", args={"path": "x.py", "content": "1"})
    )
    assert result.ok is False
    assert "may not call" in result.output
    assert not (tmp_path / "x.py").exists()  # nothing was written

    read = engine._execute_one(ToolCall(tool="read_file", args={"path": "x.py"}))
    # a real read of a genuinely missing file -> a real error, not a block
    assert read.ok is False
    assert "may not call" not in read.output


def test_build_profile_still_writes(tmp_path: Path) -> None:
    engine = _engine(tmp_path, agent_id="build")
    result = engine._execute_one(
        ToolCall(tool="write_file", args={"path": "ok.py", "content": "x = 1\n"})
    )
    assert result.ok
    assert (tmp_path / "ok.py").read_text(encoding="utf-8") == "x = 1\n"


def test_native_specs_exclude_whitelist_extras(tmp_path: Path) -> None:
    engine = _engine(tmp_path, agent_id="plan")
    specs = tool_specs(engine._groups(), allowed=engine.profile.tools)
    names = {s["name"] for s in specs}
    assert "write_file" not in names
    assert "read_file" in names


def test_system_prompt_carries_persona_and_restriction(tmp_path: Path) -> None:
    engine = _engine(tmp_path, agent_id="plan")
    prompt = engine.messages[0].content
    assert "ACTIVE AGENT PROFILE: Plan (plan)." in prompt
    assert "may call ONLY these tools" in prompt
    assert "read_file" in prompt  # a whitelist member is listed
    assert "write_file" not in prompt  # a rejected tool is never advertised


def test_set_agent_switches_and_re_renders(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert engine._system_prompt() == engine.messages[0].content
    profile = engine.set_agent("explore")
    assert profile.id == "explore"
    assert "ACTIVE AGENT PROFILE: Explore (explore)." in engine.messages[0].content
    assert engine.profile.id == "explore"


def test_explicit_step_budget_beats_profile(tmp_path: Path) -> None:
    engine = _engine(tmp_path, agent_id="plan", max_steps=3)
    assert engine.max_steps == 3
    engine.set_agent("build")  # explicit budget survives a switch
    assert engine.max_steps == 3
