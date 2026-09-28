"""v9.1.1 Stage 1: source stabilisation, intent-driven agent, internet access.

Covers the concrete defects fixed and features added in this stage:

* the built-in default credential is **configurable and diagnosable** (the
  build used to read only ``OPENROUTER_API_KEY`` while the docs told users to
  set ``SEEDCODE_DEFAULT_API_KEY``, so a clean install shipped with none), and
  a missing / invalid / unavailable credential is now distinguishable;
* the deterministic **intent classifier** decides what a request needs, so a
  song request never reads the workspace and a coding question never runs
  tools;
* **internet retrieval** is modular, bounded and network-free to test;
* **Chat -> Agent escalation** executes one task and returns to Chat Mode
  without changing the persistent mode;
* **permission grants persist** across engine rebuilds, and "Allow All" stops
  the repeated prompts.

No test here touches the network: the HTTP seam is always patched.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from seedcode import app, default_api
from seedcode.core import internet
from seedcode.core.intent import Intent, IntentKind, classify
from seedcode.core.models import AppConfig, Message
from seedcode.tools import TOOL_REGISTRY, get_tool
from seedcode.tools.permissions import (
    CATEGORY_DELETE,
    CATEGORY_SHELL,
    ActionGate,
    ActionGrant,
)

ROOT = Path(__file__).resolve().parents[1]
EMBED_SCRIPT = ROOT / "scripts" / "windows" / "embed_default_key.py"


def _load_embed_module():
    """Import the build script by path (it is not an importable package)."""
    spec = importlib.util.spec_from_file_location("_embed_default_key_test", EMBED_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Config: built-in default credential
# --------------------------------------------------------------------------


@pytest.fixture()
def _no_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(default_api, "_load_embedded", lambda: ("", False, "test"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SEEDCODE_DEFAULT_API_KEY", raising=False)


class TestBuiltinCredentialDiagnostics:
    def test_missing_when_nothing_is_configured(self, _no_builtin) -> None:
        status, reason = default_api.builtin_status()
        assert status == default_api.BUILTIN_MISSING
        assert "no built-in credential" in reason

    def test_available_from_the_documented_env_var(self, monkeypatch) -> None:
        monkeypatch.setattr(default_api, "_load_embedded", lambda: ("", False, "test"))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setenv("SEEDCODE_DEFAULT_API_KEY", "sk-or-v1-" + "a" * 24)
        status, reason = default_api.builtin_status()
        assert status == default_api.BUILTIN_AVAILABLE
        assert "SEEDCODE_DEFAULT_API_KEY" in reason

    def test_invalid_when_the_value_is_malformed(self, monkeypatch) -> None:
        monkeypatch.setattr(default_api, "_load_embedded", lambda: ("", False, "test"))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        # Looks like a key but is far too short: a truncated copy-paste.
        monkeypatch.setenv("SEEDCODE_DEFAULT_API_KEY", "sk-or")
        status, reason = default_api.builtin_status()
        assert status == default_api.BUILTIN_INVALID
        assert "shorter" in reason

    def test_embedded_reported_available(self, monkeypatch) -> None:
        monkeypatch.setattr(
            default_api, "_load_embedded", lambda: ("sk-or-v1-" + "b" * 24, True, "note")
        )
        assert default_api.builtin_status()[0] == default_api.BUILTIN_AVAILABLE

    def test_description_never_leaks_the_value(self, monkeypatch) -> None:
        secret = "sk-or-v1-" + "c" * 30
        monkeypatch.setattr(default_api, "_load_embedded", lambda: ("", False, "test"))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setenv("SEEDCODE_DEFAULT_API_KEY", secret)
        assert secret not in default_api.describe_builtin_status()


class TestDoctorReportsBuiltinStatus:
    def test_default_provider_reports_missing_credential(self, monkeypatch, _no_builtin) -> None:
        from seedcode.commands import doctor

        class _StubProvider:
            id = "default"
            label = "Default"
            requires_key = False

            def prepare(self, config):
                return None

            def list_models(self, config):
                return []

        monkeypatch.setattr(doctor, "get_provider", lambda pid: _StubProvider())
        rows = doctor._run_checks(AppConfig(provider="default", model="m"))
        builtin = [r for r in rows if r[1] == "Built-in connection"]
        assert builtin, rows
        assert "no built-in credential" in builtin[0][2]


class TestEmbedScriptCredentialNames:
    """The build must embed a key configured with either documented name."""

    def _generate(self, monkeypatch, tmp_path: Path, env_text: str) -> tuple[int, str]:
        mod = _load_embed_module()
        monkeypatch.setattr(mod, "TARGET", tmp_path / "_default_key.py")
        env_file = tmp_path / ".env"
        env_file.write_text(env_text, encoding="utf-8")
        code = mod.generate(env_file)
        generated = mod.TARGET.read_text(encoding="utf-8") if mod.TARGET.exists() else ""
        return code, generated

    def test_openrouter_name_still_embeds(self, monkeypatch, tmp_path: Path) -> None:
        code, generated = self._generate(
            monkeypatch, tmp_path, "OPENROUTER_API_KEY=sk-or-v1-" + "d" * 24 + "\n"
        )
        assert code == 0
        assert "DEFAULT_API_ENABLED = True" in generated

    def test_seedcode_default_name_embeds_too(self, monkeypatch, tmp_path: Path) -> None:
        """The regression: only OPENROUTER_API_KEY used to be read."""
        code, generated = self._generate(
            monkeypatch,
            tmp_path,
            "SEEDCODE_DEFAULT_API_KEY=sk-or-v1-" + "e" * 24 + "\n",
        )
        assert code == 0
        assert "DEFAULT_API_ENABLED = True" in generated

    def test_missing_reports_configuration_not_found(self, monkeypatch, tmp_path: Path, capsys) -> None:
        code, generated = self._generate(monkeypatch, tmp_path, "# nothing here\n")
        assert code == 2
        assert generated == ""
        out = capsys.readouterr().out
        assert "No built-in credential variable found" in out

    def test_typo_is_reported_with_the_fix(self, monkeypatch, tmp_path: Path, capsys) -> None:
        code, _ = self._generate(
            monkeypatch, tmp_path, "DEFULT_API_KEY=sk-or-v1-" + "f" * 24 + "\n"
        )
        assert code == 2
        out = capsys.readouterr().out
        assert "DEFULT_API_KEY" in out
        assert "SEEDCODE_DEFAULT_API_KEY" in out

    def test_invalid_value_is_refused_and_distinguished(
        self, monkeypatch, tmp_path: Path, capsys
    ) -> None:
        code, generated = self._generate(
            monkeypatch, tmp_path, "SEEDCODE_DEFAULT_API_KEY=short\n"
        )
        assert code == 2
        assert generated == ""
        assert "looks invalid" in capsys.readouterr().out

    def test_never_prints_the_key(self, monkeypatch, tmp_path: Path, capsys) -> None:
        secret = "sk-or-v1-" + "9" * 30
        code, _ = self._generate(
            monkeypatch, tmp_path, f"SEEDCODE_DEFAULT_API_KEY={secret}\n"
        )
        assert code == 0
        assert secret not in capsys.readouterr().out


# --------------------------------------------------------------------------
# Intent: what a request actually needs
# --------------------------------------------------------------------------


class TestIntentClassification:
    def test_browser_task_needs_no_workspace(self) -> None:
        intent = classify("Play this song on YouTube.")
        assert intent.kind is IntentKind.COMPUTER
        assert intent.needs_desktop and intent.needs_internet
        assert intent.needs_workspace is False
        assert intent.executes is True

    def test_open_app_is_a_computer_task(self) -> None:
        intent = classify("Open WhatsApp and check this chat.")
        assert intent.kind is IntentKind.COMPUTER
        assert intent.needs_workspace is False

    def test_coding_task_needs_the_workspace(self) -> None:
        intent = classify("Fix the provider switching bug.")
        assert intent.kind is IntentKind.CODING
        assert intent.needs_workspace is True
        assert intent.needs_desktop is False
        assert intent.executes is True

    def test_file_reference_is_a_coding_task(self) -> None:
        intent = classify("Fix the bug in src/provider.py")
        assert intent.kind is IntentKind.CODING
        assert intent.needs_workspace is True

    def test_pure_question_is_a_conversation(self) -> None:
        intent = classify("What is the capital of France?")
        assert intent.kind is IntentKind.CONVERSATION
        assert intent.executes is False
        assert not intent.needs_workspace and not intent.needs_desktop

    def test_coding_question_stays_conversational(self) -> None:
        intent = classify("How do I fix the provider switching bug?")
        assert intent.kind is IntentKind.CONVERSATION
        assert intent.executes is False

    def test_fresh_information_is_research_without_escalation(self) -> None:
        intent = classify("What is the latest version of React?")
        assert intent.kind is IntentKind.RESEARCH
        assert intent.needs_internet is True
        # Research is answerable in Chat Mode; it must not fork an agent turn.
        assert intent.executes is False

    def test_greeting_is_a_conversation(self) -> None:
        assert classify("hi there").kind is IntentKind.CONVERSATION
        assert classify("").kind is IntentKind.CONVERSATION

    def test_classification_is_deterministic(self) -> None:
        text = "Refactor the agent loop and add tests"
        assert classify(text) == classify(text)

    def test_coding_tasks_never_see_desktop_or_web_tools(self) -> None:
        intent = classify("Implement the cancellation feature")
        assert "desktop" not in intent.tool_groups
        assert "web" not in intent.tool_groups

    def test_research_may_use_web_tools_only(self) -> None:
        intent = classify("What changed in the latest Node.js release?")
        assert intent.tool_groups == ("web",)


# --------------------------------------------------------------------------
# The agent only loads the context a task needs (zero unnecessary file access)
# --------------------------------------------------------------------------


class TestAgentIntentGating:
    def _engine(self):
        from seedcode.core.agent import AgentEngine
        from seedcode.tools import PermissionManager

        return AgentEngine(
            AppConfig(provider="openrouter", model="m"),
            PermissionManager(workspace=Path.cwd()),
        )

    @staticmethod
    def _sentinel(monkeypatch) -> None:
        from seedcode.core.agent import AgentEngine

        monkeypatch.setattr(
            AgentEngine,
            "_project_context",
            lambda self: "\n\nPROJECT CONTEXT:\nSENTINEL",
        )

    def test_browser_task_carries_no_workspace_context(self, monkeypatch) -> None:
        self._sentinel(monkeypatch)
        engine = self._engine()
        engine.set_intent(classify("Play this song on YouTube."))
        prompt = engine.messages[0].content
        assert "SENTINEL" not in prompt
        assert "COMPUTER TASK" in prompt
        assert engine._workspace_allowed() is False

    def test_coding_task_keeps_workspace_context(self, monkeypatch) -> None:
        self._sentinel(monkeypatch)
        engine = self._engine()
        engine.set_intent(classify("Fix the provider switching bug."))
        assert "SENTINEL" in engine.messages[0].content
        assert engine._workspace_allowed() is True

    def test_conversation_task_gets_a_no_tools_hint(self, monkeypatch) -> None:
        self._sentinel(monkeypatch)
        engine = self._engine()
        engine.set_intent(classify("What is the capital of France?"))
        prompt = engine.messages[0].content
        assert "CONVERSATIONAL REQUEST" in prompt
        assert "SENTINEL" not in prompt

    def test_coding_turn_advertises_neither_desktop_nor_web_tools(self) -> None:
        engine = self._engine()
        engine.set_intent(classify("Implement the cancellation feature"))
        assert "desktop" not in engine._groups()
        assert "web" not in engine._groups()

    def test_research_turn_advertises_web_tools_only(self) -> None:
        engine = self._engine()
        engine.set_intent(classify("What changed in the latest Node.js release?"))
        groups = engine._groups()
        assert "web" in groups
        assert "desktop" not in groups


# --------------------------------------------------------------------------
# Internet retrieval (network always patched)
# --------------------------------------------------------------------------

_DDG_HTML = """
<div class="result__body">
  <a class="result__a" href="https://example.com/a">First &amp; Best</a>
  <a class="result__snippet" href="https://example.com/a">A useful snippet.</a>
</div>
<div class="result__body">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fb">Second</a>
  <a class="result__snippet">Another snippet.</a>
</div>
"""


class TestInternetRetrieval:
    def test_disabled_by_env(self, monkeypatch) -> None:
        monkeypatch.setenv("SEEDCODE_DISABLE_INTERNET", "1")
        assert internet.internet_disabled() is True
        assert internet.search("anything") == []
        assert internet.research("anything").ok is False

    def test_search_parses_results(self, monkeypatch) -> None:
        monkeypatch.delenv("SEEDCODE_DISABLE_INTERNET", raising=False)
        monkeypatch.setattr(internet, "_http_get", lambda url, **kw: _DDG_HTML)
        results = internet.search("example")
        assert len(results) == 2
        assert results[0].title == "First & Best"
        assert results[0].url == "https://example.com/a"
        # A wrapped DuckDuckGo redirect is unwrapped.
        assert results[1].url == "https://example.org/b"

    def test_search_never_raises_on_failure(self, monkeypatch) -> None:
        monkeypatch.delenv("SEEDCODE_DISABLE_INTERNET", raising=False)

        def boom(url, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(internet, "_http_get", boom)
        assert internet.search("x") == []

    def test_fetch_strips_markup(self, monkeypatch) -> None:
        monkeypatch.delenv("SEEDCODE_DISABLE_INTERNET", raising=False)
        monkeypatch.setattr(
            internet,
            "_http_get",
            lambda url, **kw: "<html><body><h1>Title</h1><script>x()</script><p>Body</p></body></html>",
        )
        text = internet.fetch("https://example.com")
        assert "Title" in text and "Body" in text
        assert "<p>" not in text and "x()" not in text

    def test_fetch_rejects_non_http(self) -> None:
        assert internet.fetch("file:///etc/passwd") == ""
        assert internet.fetch("") == ""

    def test_research_reads_top_sources(self, monkeypatch) -> None:
        monkeypatch.delenv("SEEDCODE_DISABLE_INTERNET", raising=False)

        def fake_get(url, **kw):
            if "duckduckgo" in url:
                return _DDG_HTML
            return "<html><body><p>Full article text</p></body></html>"

        monkeypatch.setattr(internet, "_http_get", fake_get)
        result = internet.research("example", limit=2, read_top=2)
        assert result.ok
        assert len(result.results) == 2
        assert result.excerpts
        assert "Full article text" in result.excerpts[0][1]

    def test_context_block_is_bounded(self, monkeypatch) -> None:
        monkeypatch.delenv("SEEDCODE_DISABLE_INTERNET", raising=False)

        def fake_get(url, **kw):
            if "duckduckgo" in url:
                return _DDG_HTML
            return "<html><body>" + ("word " * 5000) + "</body></html>"

        monkeypatch.setattr(internet, "_http_get", fake_get)
        result = internet.research("example")
        block = internet.context_block(result, char_budget=1000)
        assert len(block) <= 1000

    def test_context_block_empty_when_not_ok(self) -> None:
        assert internet.context_block(internet.ResearchResult(query="q")) == ""


class TestWebTools:
    def test_tools_are_registered_in_the_web_group(self) -> None:
        assert get_tool("web_search").group == "web"
        assert get_tool("web_fetch").group == "web"
        assert TOOL_REGISTRY["web_search"].mutates is False

    def test_web_search_reports_disabled(self, monkeypatch) -> None:
        from seedcode.tools.permissions import PermissionManager

        monkeypatch.setenv("SEEDCODE_DISABLE_INTERNET", "1")
        perm = PermissionManager(workspace=Path.cwd())
        result = get_tool("web_search").run(perm, {"query": "python"})
        assert result.ok is False
        assert "disabled" in result.output.lower()

    def test_web_search_returns_results(self, monkeypatch) -> None:
        from seedcode.tools.permissions import PermissionManager

        monkeypatch.delenv("SEEDCODE_DISABLE_INTERNET", raising=False)
        monkeypatch.setattr(internet, "_http_get", lambda url, **kw: _DDG_HTML)
        perm = PermissionManager(workspace=Path.cwd())
        result = get_tool("web_search").run(perm, {"query": "example"})
        assert result.ok
        assert "https://example.com/a" in result.output

    def test_web_search_requires_a_query(self) -> None:
        from seedcode.tools.base import ToolError
        from seedcode.tools.permissions import PermissionManager

        perm = PermissionManager(workspace=Path.cwd())
        with pytest.raises(ToolError):
            get_tool("web_search").run(perm, {})


# --------------------------------------------------------------------------
# Chat -> Agent escalation (temporary) and Agent Mode persistence
# --------------------------------------------------------------------------


class _RecordingAgent:
    """Agent double: records turns, reports a changed file as evidence."""

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.config = AppConfig(agent_mode=True)
        self.messages = [Message(role="system", content="system")]
        self.transcript = list(self.messages)
        self.intent = None

    def set_intent(self, intent) -> None:
        self.intent = intent

    def run_turn(self, text: str) -> str:
        self.calls.append(text)
        return f"Finished: {text}"


class _ScriptedSession:
    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def prompt(self, message=None, **_kwargs) -> str:
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


class _FakeHistory:
    def __init__(self) -> None:
        self.saved = 0

    def save(self, transcript) -> None:
        self.saved += 1


# The recording console the UI prints into (no real terminal involved).
def _console(width: int = 100):
    import io

    from rich.console import Console

    from seedcode.ui.theme import SEED_THEME

    return Console(
        theme=SEED_THEME,
        width=width,
        file=io.StringIO(),
        force_terminal=False,
        legacy_windows=False,
        record=True,
        highlight=False,
    )


def _ui():
    from seedcode.ui import UI

    ui = UI(plain=True)
    ui.console = _console()
    return ui


def test_chat_mode_escalates_then_returns_without_changing_mode(monkeypatch) -> None:
    from seedcode.core.chat import ChatEngine

    agent_calls: list[str] = []
    chat_calls: list[str] = []
    monkeypatch.setattr(
        app,
        "_make_agent",
        lambda ui, config, presenter=None: _RecordingAgent(agent_calls),
    )
    monkeypatch.setattr(
        app, "_handle_chat", lambda ui, engine, history, text, intent=None: chat_calls.append(text)
    )
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=False)
    config.set_api_key("openrouter", "sk-or-test")
    ui = _ui()
    session = _ScriptedSession(["Play this song on YouTube.", "/exit"])

    app._chat_loop(ui, config, ChatEngine(config), _FakeHistory(), session)

    assert agent_calls == ["Play this song on YouTube."]
    assert chat_calls == []  # never fell back to a chat answer
    # The persistent mode is untouched: agent_mode was never flipped on.
    assert config.mode == "chat"
    out = ui.console.export_text()
    assert "Agent assist" in out
    assert "back to Chat Mode" in out


def test_conversation_does_not_escalate(monkeypatch) -> None:
    from seedcode.core.chat import ChatEngine

    agent_calls: list[str] = []
    chat_calls: list[str] = []
    monkeypatch.setattr(
        app,
        "_make_agent",
        lambda ui, config, presenter=None: _RecordingAgent(agent_calls),
    )
    monkeypatch.setattr(
        app, "_handle_chat", lambda ui, engine, history, text, intent=None: chat_calls.append(text)
    )
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=False)
    config.set_api_key("openrouter", "sk-or-test")
    ui = _ui()
    session = _ScriptedSession(["Hello there", "/exit"])

    app._chat_loop(ui, config, ChatEngine(config), _FakeHistory(), session)

    assert agent_calls == []
    assert chat_calls == ["Hello there"]
    assert "Agent assist" not in ui.console.export_text()


def test_explicit_agent_mode_stays_on_after_a_task(monkeypatch) -> None:
    from seedcode.core.chat import ChatEngine

    calls: list[str] = []
    monkeypatch.setattr(
        app, "_make_agent", lambda ui, config, presenter=None: _RecordingAgent(calls)
    )
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    session = _ScriptedSession(["first task", "second task", "/exit"])
    monkeypatch.setattr(
        app, "_handle_chat", lambda ui, engine, history, text, intent=None: None
    )

    app._chat_loop(_ui(), config, ChatEngine(config), _FakeHistory(), session)

    assert calls == ["first task", "second task"]
    assert config.mode == "agent"


# --------------------------------------------------------------------------
# Permission persistence and the Allow All scope
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_gate():
    app.reset_action_gate()
    yield
    app.reset_action_gate()


class _ScriptedUI:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def confirm_tool_action(self, label: str, description: str, **_kw) -> str:
        self.prompts.append(description)
        if not self.answers:
            pytest.fail("permission prompted more times than scripted")
        return self.answers.pop(0)


class TestPermissionPersistence:
    def test_allow_all_survives_engine_rebuilds(self) -> None:
        """Rebuilding the agent must not reset the user's Allow All answer."""
        ui = _ScriptedUI(["A"])
        gate = app._make_action_gate(ui)
        gate.check(CATEGORY_SHELL, "echo 1")
        # A rebuild (permission-mode change) returns the SAME session gate.
        rebuilt = app._make_action_gate(ui)
        assert rebuilt is gate
        rebuilt.check(CATEGORY_DELETE, "rm x")  # no second prompt
        rebuilt.check(CATEGORY_SHELL, "echo 2")
        assert len(ui.prompts) == 1

    def test_always_is_scoped_to_one_category(self) -> None:
        ui = _ScriptedUI(["a", "y"])
        gate = app._make_action_gate(ui)
        gate.check(CATEGORY_SHELL, "echo 1")
        gate.check(CATEGORY_SHELL, "echo 2")  # remembered
        gate.check(CATEGORY_DELETE, "rm x")   # different category prompts
        assert len(ui.prompts) == 2

    def test_gate_is_shared_across_permission_managers(self) -> None:
        from seedcode.tools import PermissionManager

        ui = _ScriptedUI(["A"])
        gate = app._make_action_gate(ui)
        perm_a = PermissionManager(workspace=Path.cwd())
        perm_a.gate = gate
        perm_b = PermissionManager(workspace=Path.cwd())
        perm_b.gate = app._make_action_gate(ui)

        perm_a.confirm_action(CATEGORY_SHELL, "echo 1")
        perm_b.confirm_action(CATEGORY_DELETE, "rm x")  # no prompt
        assert ui.prompts == ["echo 1"]

    def test_reset_forgets_allow_all(self) -> None:
        ui = _ScriptedUI(["A", "y"])
        gate = app._make_action_gate(ui)
        gate.check(CATEGORY_SHELL, "echo 1")
        app.reset_action_gate()
        fresh = app._make_action_gate(ui)
        assert fresh is not gate
        fresh.check(CATEGORY_SHELL, "echo 2")
        assert len(ui.prompts) == 2


class TestActionGateAllScope:
    def test_all_grant_approves_every_category(self) -> None:
        seen: list[str] = []

        def confirm(category: str, description: str) -> ActionGrant:
            seen.append(category)
            return ActionGrant.ALL

        gate = ActionGate(confirm=confirm)
        gate.check(CATEGORY_SHELL, "echo 1")
        gate.check(CATEGORY_DELETE, "rm x")
        assert seen == [CATEGORY_SHELL]  # only the first asked

    def test_reset_clears_allow_all(self) -> None:
        calls = 0

        def confirm(category: str, description: str) -> ActionGrant:
            nonlocal calls
            calls += 1
            return ActionGrant.ALL

        gate = ActionGate(confirm=confirm)
        gate.check(CATEGORY_SHELL, "echo 1")
        gate.reset()
        gate.check(CATEGORY_SHELL, "echo 2")
        assert calls == 2

    def test_deny_still_blocks_after_allow_all_reset(self) -> None:
        from seedcode.tools.permissions import PermissionError_

        gate = ActionGate(confirm=lambda c, d: ActionGrant.DENY)
        with pytest.raises(PermissionError_):
            gate.check(CATEGORY_SHELL, "echo 1")


# --------------------------------------------------------------------------
# Provider failover records its reason
# --------------------------------------------------------------------------


class TestFailoverReason:
    def test_switch_records_a_human_reason(self) -> None:
        from seedcode.core.chat import ChatEngine
        from seedcode.core.providers.failover import Candidate

        config = AppConfig(provider="openrouter", model="m")
        engine = ChatEngine(config)
        engine._switch_provider(Candidate("ollama", "llama3"), reason="rate limited")
        assert engine.last_switch == ("openrouter", "ollama")
        assert engine.last_switch_reason == "rate limited"

    def test_failover_reason_classifies_the_state(self) -> None:
        from seedcode.core.chat import ChatEngine
        from seedcode.core.providers.health import HealthState

        engine = ChatEngine(AppConfig(provider="openrouter", model="m"))
        assert "rate limited" in engine._failover_reason(None, HealthState.RATE_LIMITED)
        assert "authentication" in engine._failover_reason(
            None, HealthState.AUTHENTICATION_ERROR
        )
        assert "network" in engine._failover_reason(None, HealthState.OFFLINE)
