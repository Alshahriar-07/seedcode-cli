"""v9.2.0 model thinking stream (Phase 6 / Phase 11).

The thinking panel shows ONLY what the model itself exposes (Ollama's
``thinking`` field on a thinking-capable model). These tests lock in:

* the wire event exists and consumers can distinguish it from answer text;
* Ollama requests the thinking channel only when ``/api/show`` reports the
  ``thinking`` capability, and degrades gracefully when refused at runtime;
* thinking deltas are relayed by the agent as their own event kind (never
  counted as answer content);
* the presenter feeds the TUI's bounded thinking buffer, and the buffer
  stays bounded with follow/manual-scroll semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedcode.core import providers as providers_mod
from seedcode.core.agent import AgentEngine
from seedcode.core.models import AppConfig
from seedcode.core.providers.base import ThinkingDelta
from seedcode.app import _TaskPresenter
from seedcode.tools import PermissionLevel, PermissionManager
from seedcode.ui.state import AppState
from seedcode.ui.tui import ChatTUI


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------
def test_thinking_delta_is_a_distinct_stream_event() -> None:
    event = ThinkingDelta("reasoning line")
    assert event.text == "reasoning line"
    from seedcode.core.providers.base import StreamEvent, TextDelta, ToolCallEvent

    assert ThinkingDelta in StreamEvent.__args__  # type: ignore[attr-defined]
    assert isinstance(event, ThinkingDelta)
    assert not isinstance(event, (TextDelta, ToolCallEvent))


# ---------------------------------------------------------------------------
# Ollama: capability detection, the think flag, and ThinkingDelta emission
# ---------------------------------------------------------------------------
class _FakeStreamResponse:
    def __init__(self, status_code: int, lines: list[str], body: bytes = b"") -> None:
        self.status_code = status_code
        self._lines = lines
        self._body = body

    def iter_lines(self):
        return iter(self._lines)

    def read(self) -> bytes:
        return self._body


class _FakeHttp:
    """Stand-in for ``seedcode.core.http`` (post + a streaming context)."""

    def __init__(self, show_response=None, chat_lines=None, chat_status: int = 200) -> None:
        self.show_response = show_response
        self.chat_lines = chat_lines or []
        self.chat_status = chat_status
        self.show_payloads: list[dict] = []
        self.chat_payloads: list[dict] = []
        self.calls = 0

    def post(self, url, json=None, timeout=None):
        self.calls += 1
        assert url.endswith("/api/show")
        self.show_payloads.append(json or {})
        return self.show_response

    def stream(self, method, url, json=None, timeout=None):
        self.chat_payloads.append(json or {})

        class _Ctx:
            def __enter__(self_inner):
                return _FakeStreamResponse(self.chat_status, self.chat_lines)

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()


@pytest.fixture()
def ollama_mod(monkeypatch):
    module = providers_mod.ollama
    monkeypatch.setattr(module, "_thinking_cache", {}, raising=False)
    return module


def _messages():
    from seedcode.core.models import Message

    return [Message(role="user", content="do the thing")]


def test_ollama_requests_thinking_only_for_capable_models(ollama_mod, monkeypatch):
    from seedcode.core.models import AppConfig

    show = type(
        "R", (), {"status_code": 200, "json": (lambda self: {"capabilities": ["thinking"]})}
    )()
    http = _FakeHttp(
        show_response=show,
        chat_lines=['{"message": {"content": "ok"}, "done": false}', '{"done": true}'],
    )
    monkeypatch.setattr(ollama_mod, "pooled_http", http)
    config = AppConfig(provider="ollama", model="my-model")
    config.ollama_host = "http://127.0.0.1:11434"
    events = list(ollama_mod.OllamaProvider().stream_chat_with_tools(config, _messages(), []))

    # The think flag rode along exactly because /api/show reported thinking.
    assert http.chat_payloads[0]["think"] is True
    assert http.calls == 1


def test_ollama_skips_thinking_flag_without_the_capability(ollama_mod, monkeypatch):
    from seedcode.core.models import AppConfig

    show = type("R", (), {"status_code": 200, "json": (lambda self: {"capabilities": []})})()
    http = _FakeHttp(
        show_response=show,
        chat_lines=['{"message": {"content": "hi"}, "done": true}'],
    )
    monkeypatch.setattr(ollama_mod, "pooled_http", http)
    config = AppConfig(provider="ollama", model="plain-model")
    config.ollama_host = "http://127.0.0.1:11434"
    list(ollama_mod.OllamaProvider().stream_chat_with_tools(config, _messages(), []))

    assert "think" not in http.chat_payloads[0]


def test_ollama_stream_yields_thinking_and_text_separately(ollama_mod, monkeypatch):
    from seedcode.core.models import AppConfig
    from seedcode.core.providers.base import TextDelta, ToolCallEvent

    show = type(
        "R", (), {"status_code": 200, "json": (lambda self: {"capabilities": ["thinking"]})}
    )()
    http = _FakeHttp(
        show_response=show,
        chat_lines=[
            '{"message": {"thinking": "Inspect the layout...", "content": ""}, "done": false}',
            '{"message": {"thinking": "Then wiring the panel.", "content": "Here we go"}}',
            '{"done": true}',
        ],
    )
    monkeypatch.setattr(ollama_mod, "pooled_http", http)
    config = AppConfig(provider="ollama", model="my-model")
    config.ollama_host = "http://127.0.0.1:11434"
    events = list(ollama_mod.OllamaProvider().stream_chat_with_tools(config, _messages(), []))

    kinds = [type(e) for e in events]
    assert kinds == [ThinkingDelta, ThinkingDelta, TextDelta]
    assert events[0].text == "Inspect the layout..."
    assert events[-1].text == "Here we go"
    assert not any(isinstance(e, ToolCallEvent) for e in events)


def test_ollama_rejected_think_degrades_gracefully(ollama_mod, monkeypatch):
    from seedcode.core.models import AppConfig
    from seedcode.core.providers.base import ProviderError

    show = type(
        "R", (), {"status_code": 200, "json": (lambda self: {"capabilities": ["thinking"]})}
    )()
    http = _FakeHttp(show_response=show, chat_status=400)
    http.chat_lines = []
    # The 400 branch reads the body: Ollama refuses ``think``.
    streaming_status = 400

    class _Stream:
        status_code = streaming_status

        def read(self):
            return b"model does not support think"

        def iter_lines(self):
            return iter([])

    def stream(method, url, json=None, timeout=None):
        http.chat_payloads.append(json or {})

        class _Ctx:
            def __enter__(self_inner):
                return _Stream()

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()

    http.stream = stream  # type: ignore[method-assign]
    monkeypatch.setattr(ollama_mod, "pooled_http", http)
    config = AppConfig(provider="ollama", model="lied-model")
    config.ollama_host = "http://127.0.0.1:11434"

    with pytest.raises(ProviderError) as excinfo:
        list(ollama_mod.OllamaProvider().stream_chat_with_tools(config, _messages(), []))
    assert excinfo.value.transient is True
    assert "thinking" in str(excinfo.value)
    # Cached as unsupported: the agent's retry goes out WITHOUT the flag.
    assert ollama_mod._thinking_cache[(config.ollama_host, config.model)] is False


# ---------------------------------------------------------------------------
# Agent: ThinkingDelta is relayed as its own event kind
# ---------------------------------------------------------------------------
def _relay_engine(tmp_path: Path, events: list) -> AgentEngine:
    config = AppConfig(provider="openrouter", model="test/model", agent_mode=True)
    config.set_api_key("openrouter", "sk-or-test")
    perm = PermissionManager(workspace=tmp_path, level=PermissionLevel.WORKSPACE)
    engine = AgentEngine(config, perm, on_event=lambda k, d: events.append((k, d)))
    return engine


def test_native_step_relays_thinking_but_not_as_answer(tmp_path: Path) -> None:
    from seedcode.core.providers.base import TextDelta

    events: list[tuple[str, str]] = []
    engine = _relay_engine(tmp_path, events)
    engine._native = True

    def _reply_events(specs):
        return iter(
            [
                ThinkingDelta("read the module layout first"),
                ThinkingDelta("then patch the dispatcher"),
                TextDelta("Done: patched the dispatcher."),
            ]
        )

    engine.stream_reply_events = _reply_events  # type: ignore[method-assign]

    outcome, reply = engine._native_step(0)

    assert outcome == "final" and reply == "Done: patched the dispatcher."
    thinking_events = [d for k, d in events if k == "thinking"]
    assert thinking_events == [
        "read the module layout first",
        "then patch the dispatcher",
    ]
    assert all(d not in (reply or "") for d in thinking_events)


# ---------------------------------------------------------------------------
# Presenter + AppState thinking buffer
# ---------------------------------------------------------------------------
def test_presenter_streams_thinking_into_the_tui_state() -> None:
    state = AppState()
    tui = ChatTUI(state, width=80, height=24)

    class FakeUI:
        is_tui = True

    FakeUI.tui = tui
    presenter = _TaskPresenter(FakeUI())
    presenter.on_event("thinking", "checking the registry")
    presenter.on_event("thinking", "\nthen the dispatcher")

    assert "checking the registry" in state.thinking_text
    assert "then the dispatcher" in state.thinking_text
    assert state.thinking_open is True


def test_thinking_buffer_stays_bounded_and_follows_the_bottom() -> None:
    state = AppState()
    state.reset_thinking()
    for i in range(2000):
        state.append_thinking(f"line-{i} " + "x" * 30)

    assert len(state.thinking_text) <= AppState.THINKING_BUFFER_LIMIT + 200
    assert state.thinking_follow() is True  # auto-scrolls while offset == 0

    state.scroll_thinking(5)
    assert state.thinking_offset == 5
    assert state.thinking_follow() is False  # user position is kept

    state.toggle_thinking()
    assert state.thinking_open is False
    state.toggle_thinking()
    assert state.thinking_open is True and state.thinking_offset == 0
