"""v10.2.0 execution-reliability contract.

These tests lock in the backend rules that make a small or unreliable model
usable without trusting its claims:

* model-aware profiles and their output budget;
* the ``TOOL_CALL:`` text protocol and strict argument validation;
* no recognized tool-call JSON leaking into the transcript;
* continuation of an output-limited response without duplication;
* a completion claim after a failed tool being sent back for verification;
* provider/model failover that respects the local-to-cloud privacy gate;
* the Seed palette, full-width layout and inline task progress.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedcode import __version__
from seedcode.core.agent import (
    AgentEngine,
    ToolCall,
    parse_tool_calls,
    strip_tool_blocks,
    validate_tool_call,
    visible_reply,
)
from seedcode.core.chat import ChatEngine
from seedcode.core.model_profiles import (
    PROFILES,
    output_token_cap,
    profile_for,
)
from seedcode.core.models import AppConfig
from seedcode.core.tasks import TurnEvidence
from seedcode.tools import PermissionLevel, PermissionManager
from seedcode.ui.state import AppState
from seedcode.ui.theme import PALETTES, active_palette, rich_theme, set_active_theme
from seedcode.ui.tui import ChatTUI


def _engine(tmp_path: Path, **kwargs) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model", **kwargs)
    config.set_api_key("openrouter", "sk-or-test")
    permissions = PermissionManager(
        workspace=tmp_path, level=PermissionLevel.WORKSPACE
    )
    return AgentEngine(config, permissions)


# --- 1. model-aware profiles --------------------------------------------------
class TestModelProfiles:
    def test_explicit_profile_wins(self) -> None:
        config = AppConfig(provider="openrouter", model="x", prompt_profile="small")
        assert profile_for(config).id == "small"
        config.prompt_profile = "advanced"
        assert profile_for(config).id == "advanced"

    def test_model_map_overrides_auto_detection(self) -> None:
        config = AppConfig(
            provider="ollama",
            model="qwen3:4b",
            model_profiles={"qwen3:4b": "advanced"},
        )
        assert profile_for(config).id == "advanced"

    def test_local_small_model_gets_compact_profile(self) -> None:
        config = AppConfig(provider="ollama", model="qwen3:4b")
        assert profile_for(config).id == "small"
        # Unknown local size is conservatively compact.
        assert profile_for(config, model="my-custom-model").id == "small"
        # A larger declared model is not reduced to the small profile.
        assert profile_for(config, model="llama3.1:70b").id == "advanced"

    def test_cloud_model_defaults_to_standard(self) -> None:
        config = AppConfig(provider="openrouter", model="vendor/model")
        assert profile_for(config).id == "standard"

    def test_small_profile_caps_output_tokens(self) -> None:
        small = AppConfig(provider="ollama", model="qwen3:4b", agent_mode=True)
        standard = AppConfig(provider="openrouter", model="vendor/model", agent_mode=True)
        small.max_tokens = 128000
        standard.max_tokens = 128000
        assert small.effective_max_tokens() <= PROFILES["small"].max_output_tokens
        assert output_token_cap(small) == PROFILES["small"].max_output_tokens
        assert output_token_cap(standard) == 0

    def test_profile_budgets_are_configured_on_the_engine(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        assert engine.model_profile.id == "standard"
        engine.config.prompt_profile = "small"
        engine._apply_profile_steps()
        assert engine._max_calls_per_step() == PROFILES["small"].max_calls_per_step

    def test_config_overrides_steps_and_calls(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.config.max_agent_steps = 7
        engine.config.max_tool_calls_per_step = 2
        engine._apply_profile_steps()
        assert engine.max_steps == 7
        assert engine._max_calls_per_step() == 2


# --- 2. tool protocol ---------------------------------------------------------
class TestToolProtocol:
    def test_tool_call_marker_is_parsed(self) -> None:
        text = (
            'TOOL_CALL: {"name": "read_file", "arguments": {"path": "app.py"}}\n'
            'TOOL_CALL: {"tool": "list_dir", "args": {"path": "."}}'
        )
        calls = parse_tool_calls(text)
        assert [c.tool for c in calls] == ["read_file", "list_dir"]
        assert calls[0].args == {"path": "app.py"}

    def test_marker_inside_a_code_fence_is_an_example(self) -> None:
        text = (
            "Here is the protocol:\n```\n"
            'TOOL_CALL: {"name": "delete_file", "arguments": {"path": "x"}}\n'
            "```\n"
        )
        assert parse_tool_calls(text) == []

    def test_malformed_marker_is_reported_not_executed(self) -> None:
        calls = parse_tool_calls('TOOL_CALL: {"name": "read_file", "arguments": {')
        assert len(calls) == 1
        assert not calls[0].tool
        assert "Invalid JSON" in calls[0].error

    def test_identical_calls_in_one_reply_are_deduped(self) -> None:
        line = 'TOOL_CALL: {"name": "list_dir", "arguments": {"path": "."}}'
        assert len(parse_tool_calls(f"{line}\n{line}")) == 1

    def test_unknown_tool_is_rejected_before_execution(self) -> None:
        problem = validate_tool_call(ToolCall("not_a_tool", {}))
        assert "Unknown tool" in problem

    def test_missing_required_argument_is_rejected(self) -> None:
        problem = validate_tool_call(ToolCall("read_file", {}))
        assert "missing required argument" in problem
        assert validate_tool_call(ToolCall("read_file", {"path": "a.py"})) == ""

    def test_recognized_call_json_never_leaks(self) -> None:
        marker = 'TOOL_CALL: {"name": "list_dir", "arguments": {"path": "."}}'
        assert strip_tool_blocks(marker) == ""
        assert visible_reply(marker) == ""
        native = '{"name": "list_dir", "arguments": {"path": "."}}'
        assert visible_reply(native) == ""
        prose = 'The file contains {"name": "value", "arguments": {}} as data.'
        assert "The file contains" in visible_reply(prose)


# --- 3. response termination --------------------------------------------------
class TestResponseContinuation:
    def test_empty_provider_stream_fails_explicitly(self, monkeypatch) -> None:
        from seedcode.core import chat as chat_mod
        from seedcode.core.chat import ChatError

        chat = ChatEngine(AppConfig(provider="openrouter", model="test/model"))
        monkeypatch.setattr(chat_mod, "_max_retries", lambda: 0)
        with pytest.raises(ChatError, match="ended without response data"):
            list(chat._stream_with_retry(lambda: iter(())))

    def test_output_limit_continues_without_duplicating(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        outcome, payload = engine._finish("The first half", "length")
        assert outcome == "ok" and payload is None
        assert engine._prefix_text == "The first half"
        assert engine.messages[-1].role == "user"
        assert "cut off" in engine.messages[-1].content

        outcome, payload = engine._finish(" and the second half.", "stop")
        assert outcome == "final"
        assert payload == "The first half and the second half."
        assert engine._prefix_text == ""

    def test_continuation_is_bounded(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        for _ in range(PROFILES["standard"].continuation_limit):
            assert engine._finish("part", "length")[0] == "ok"
        assert engine._finish("last", "length")[0] == "final"

    def test_continuation_can_be_disabled(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.config.continue_truncated = False
        outcome, payload = engine._finish("truncated", "length")
        assert outcome == "final"
        assert payload == "truncated"

    def test_unterminated_fence_looks_truncated(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        assert engine._looks_truncated("```python\nprint(1)")
        assert not engine._looks_truncated("```python\nprint(1)\n```")


class TestCompletionGate:
    def test_completion_claim_after_failure_requires_verification(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        engine.last_evidence = TurnEvidence(tools=1)
        engine.last_evidence.note_error("run_command: failed (exit code 1)")

        outcome, _payload = engine._finish("The task is complete.", "stop")
        assert outcome == "ok"
        assert engine.messages[-1].content.startswith("[SYSTEM] A tool call failed")
        assert engine._completion_nudged

        # Bounded: the second claim is accepted so the turn cannot loop.
        assert engine._finish("The task is complete.", "stop")[0] == "final"

    def test_failure_without_a_claim_is_not_nudged(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.last_evidence = TurnEvidence(tools=1)
        engine.last_evidence.note_error("run_command: failed (exit code 1)")
        outcome, payload = engine._finish(
            "The build failed because the import is missing.", "stop"
        )
        assert outcome == "final"
        assert "build failed" in payload


# --- 4. failover and privacy --------------------------------------------------
class TestFailoverPrivacy:
    def _chat(self, *, allow_cloud: bool) -> ChatEngine:
        config = AppConfig(provider="ollama", model="qwen3:4b", auto_failover=True)
        config.allow_local_to_cloud = allow_cloud
        return ChatEngine(config, failover=True)

    def test_local_session_stays_local_by_default(self, monkeypatch) -> None:
        from seedcode.core import chat as chat_mod

        class FakeChain:
            def __init__(self, config) -> None:
                pass

            def candidates(self, *, exclude=()):
                from seedcode.core.providers.failover import Candidate

                return [Candidate("openrouter", "vendor/model")]

        monkeypatch.setattr(chat_mod, "FailoverChain", FakeChain)
        chat = self._chat(allow_cloud=False)
        assert chat._next_provider(set()) is None

    def test_explicit_opt_in_allows_a_cloud_candidate(self, monkeypatch) -> None:
        from seedcode.core import chat as chat_mod

        class FakeChain:
            def __init__(self, config) -> None:
                pass

            def candidates(self, *, exclude=()):
                from seedcode.core.providers.failover import Candidate

                return [Candidate("openrouter", "vendor/model")]

        monkeypatch.setattr(chat_mod, "FailoverChain", FakeChain)
        chat = self._chat(allow_cloud=True)
        candidate = chat._next_provider(set())
        assert candidate is not None and candidate.provider_id == "openrouter"

    def test_fallback_models_are_explicit_only(self) -> None:
        chat = self._chat(allow_cloud=False)
        assert chat._next_model(set()) is None
        chat.config.fallback_models = ["qwen3:4b", "llama3.1:8b"]
        assert chat._next_model(set()) == "qwen3:4b"
        assert chat._next_model({"qwen3:4b"}) == "llama3.1:8b"


# --- 5. UI identity and layout ------------------------------------------------
class TestSeedInterface:
    def test_seed_palette_matches_the_v102_identity(self) -> None:
        seed = PALETTES["seed"]
        assert seed.primary == "#22C55E"
        assert seed.secondary == "#16A34A"
        assert seed.accent == "#4ADE80"
        assert seed.text == "#FFFFFF"
        assert seed.dim == "#A1A1AA"
        assert seed.muted == "#71717A"
        assert seed.border == "#27272A"
        assert seed.error == "#EF4444"
        assert seed.warning == "#F59E0B"
        assert seed.info == "#3B82F6"

    def test_rich_theme_uses_the_seed_roles(self) -> None:
        set_active_theme("seed")
        assert active_palette().primary == "#22C55E"
        style = rich_theme("seed").styles["seed.primary"]
        assert "22c55e" in str(style).lower()

    def test_plan_is_inline_and_the_conversation_is_full_width(self) -> None:
        tui = ChatTUI(AppState(), width=100, height=30)
        tui.state.set_plan(
            [("completed", "Inspect project"), ("running", "Implement changes")]
        )
        lines = tui._slice(100, 20)
        text = "\n".join("".join(part for _style, part in line) for line in lines)
        assert "Task Progress" in text
        assert "Inspect project" in text
        assert tui._app is None  # no panel window is built before the app exists
        # The right-side plan column is gone from the layout builder.
        assert tui._plan_window is None

    def test_version_is_10_2_0(self) -> None:
        assert __version__ == "10.2.0"
