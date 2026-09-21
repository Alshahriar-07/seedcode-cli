"""v6.2.5 UX + API regression tests.

Covers the additions that completed the v6.2.5 pass:

* ``/chat`` and ``/mode`` (mode switching, invalid-argument errors),
* the ``/permissions`` alias of ``/permission``,
* HTTP 429 ``Retry-After`` handling (parse, cap, honor during retry),
* the configurable retry cap (``SEEDCODE_MAX_RETRIES``),
* ``SEEDCODE_PROVIDER`` / ``SEEDCODE_MODEL`` environment preseeding,
* ``seedcode --version`` output.

No network: every provider call is faked.
"""

from __future__ import annotations

import pytest

from seedcode import defaults
from seedcode.commands import _ALIASES, CommandContext, dispatch
from seedcode.config import manager
from seedcode.core.chat import ChatEngine, ChatError, _max_retries
from seedcode.core.models import AppConfig
from seedcode.core.providers.base import ProviderError
from seedcode.core.providers.openrouter import _rate_limited, _retry_after_seconds


class _StubUI:
    """Records every message the UI would print."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.panels: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(str(message))

    def dim(self, message: str) -> None:
        self.messages.append(str(message))

    def success(self, message: str) -> None:
        self.messages.append(str(message))

    def warning(self, message: str) -> None:
        self.messages.append(str(message))

    def error(self, message: str) -> None:
        self.messages.append(str(message))

    def panel(self, body, title: str | None = None) -> None:
        self.panels.append(title or "")

    def blank(self) -> None:
        self.messages.append("")

    def confirm_desktop(self, category_label: str, description: str) -> str:
        return "n"


def _ctx(config: AppConfig | None = None) -> tuple[_StubUI, CommandContext]:
    ui = _StubUI()
    return ui, CommandContext(ui=ui, config=config or AppConfig(), engine=None)


# --- /chat ---------------------------------------------------------------------


def test_chat_on_returns_to_plain_chat(monkeypatch, tmp_path) -> None:
    """`/chat on` must disable Assist and Code Mode, not just print text."""
    from seedcode import codemode_state as cms
    from seedcode.commands import assist as assist_cmd
    from seedcode.commands import codemode as codemode_cmd

    cms.reset()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(codemode_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "is_available", lambda: (False, "test"))
    try:
        ui, ctx = _ctx()
        dispatch(ctx, "/codemode on")
        assert cms.codemode_state().enabled
        assert ctx.config.agent_mode

        dispatch(ctx, "/chat on")

        assert not cms.codemode_state().enabled
        assert not ctx.config.agent_mode
        assert any("Chat Mode ON" in m for m in ui.messages)
    finally:
        cms.reset()


def test_chat_bare_shows_current_mode() -> None:
    ui, ctx = _ctx()
    dispatch(ctx, "/chat")
    assert any(m.startswith("Mode:") for m in ui.messages)
    assert any("Mode: Chat" in m for m in ui.messages)


def test_chat_rejects_invalid_argument() -> None:
    ui, ctx = _ctx()
    dispatch(ctx, "/chat banana")
    assert any("[Command Error]" in m for m in ui.messages)
    assert any("Expected: /chat [on]" in m for m in ui.messages)


def test_chat_on_is_idempotent_when_already_in_chat() -> None:
    ui, ctx = _ctx()
    dispatch(ctx, "/chat on")
    assert any("Chat Mode ON" in m for m in ui.messages)


# --- /mode ---------------------------------------------------------------------


def test_mode_bare_shows_current_mode() -> None:
    ui, ctx = _ctx()
    dispatch(ctx, "/mode")
    assert any(m.startswith("Mode:") for m in ui.messages)


def test_mode_chat_switches_to_chat(monkeypatch, tmp_path) -> None:
    from seedcode.commands import assist as assist_cmd

    monkeypatch.setattr(assist_cmd, "save_config", lambda config: None)
    config = AppConfig()
    config.agent_mode = True
    ui, ctx = _ctx(config)
    dispatch(ctx, "/mode chat")
    assert not config.agent_mode
    assert any("Chat Mode ON" in m for m in ui.messages)


def test_mode_code_enables_code_mode(monkeypatch, tmp_path) -> None:
    from seedcode import codemode_state as cms
    from seedcode.commands import assist as assist_cmd
    from seedcode.commands import codemode as codemode_cmd

    cms.reset()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(codemode_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "is_available", lambda: (False, "test"))
    try:
        ui, ctx = _ctx()
        dispatch(ctx, "/mode code")
        assert cms.codemode_state().enabled
        assert any("Code Mode ON" in m for m in ui.messages)
    finally:
        cms.reset()


def test_mode_agent_routes_to_assist(monkeypatch) -> None:
    from seedcode.commands import assist as assist_cmd

    monkeypatch.setattr(assist_cmd, "save_config", lambda config: None)
    monkeypatch.setattr(assist_cmd, "is_available", lambda: (False, "test"))
    ui, ctx = _ctx()
    dispatch(ctx, "/mode agent")
    assert ctx.config.agent_mode
    assert any("Assist Mode ON" in m for m in ui.messages)


def test_mode_rejects_unknown_mode() -> None:
    ui, ctx = _ctx()
    dispatch(ctx, "/mode banana")
    assert any("[Command Error]" in m for m in ui.messages)
    assert any("chat|assist|code|agent" in m for m in ui.messages)


def test_status_and_mode_agree_on_the_mode_label() -> None:
    """One mode label source: /mode and /status must name it identically."""
    from seedcode.commands.status import mode_label

    config = AppConfig()
    ui, ctx = _ctx(config)
    dispatch(ctx, "/mode")
    label = mode_label(config)
    assert any(f"Mode: {label}" in m for m in ui.messages)


# --- /permissions alias ----------------------------------------------------------


def test_permissions_alias_is_registered() -> None:
    assert _ALIASES.get("permissions") == "permission"


def test_permissions_alias_sets_the_level() -> None:
    config = AppConfig()
    ui, ctx = _ctx(config)
    dispatch(ctx, "/permissions workspace")
    assert config.permission_mode == "workspace"
    assert any("Permission mode set" in m for m in ui.messages)


# --- HTTP 429 / Retry-After ------------------------------------------------------


class _Resp:
    """Minimal stand-in for an httpx response (headers only)."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_retry_after_parses_numeric_header() -> None:
    exc = type("Fake429", (), {"response": _Resp({"retry-after": "4"})})()
    assert _retry_after_seconds(exc) == 4.0


def test_retry_after_caps_absurd_values() -> None:
    exc = type("Fake429", (), {"response": _Resp({"retry-after": "99999"})})()
    assert _retry_after_seconds(exc) == 120.0


def test_retry_after_returns_none_for_garbage_or_missing() -> None:
    garbage = type("Fake429", (), {"response": _Resp({"retry-after": "soon"})})()
    assert _retry_after_seconds(garbage) is None
    missing = type("Fake429", (), {"response": _Resp({})})()
    assert _retry_after_seconds(missing) is None
    no_response = type("Fake429", (), {})()
    assert _retry_after_seconds(no_response) is None
    negative = type("Fake429", (), {"response": _Resp({"retry-after": "-3"})})()
    assert _retry_after_seconds(negative) is None


def test_rate_limited_error_is_transient_and_specific() -> None:
    exc = type("Fake429", (), {"response": _Resp({"retry-after": "4"})})()
    err = _rate_limited(exc)
    assert err.transient
    assert err.retry_after == 4.0
    assert "HTTP 429" in str(err)


def test_rate_limited_without_header_still_specific() -> None:
    err = _rate_limited(type("Fake429", (), {})())
    assert err.transient
    assert err.retry_after is None
    assert "HTTP 429" in str(err)


# --- retry policy -----------------------------------------------------------------


class _FlakyProvider:
    """Yields 'ok' on the n-th call; raises a transient error before that."""

    def __init__(self, failures: int, retry_after: float | None = None) -> None:
        self.failures = failures
        self.retry_after = retry_after
        self.calls = 0

    def stream_chat(self, config, messages):  # noqa: ANN001
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderError(
                "Rate limited by OpenRouter (HTTP 429).",
                transient=True,
                retry_after=self.retry_after,
            )
        yield "ok"


def _engine_with(provider: _FlakyProvider) -> ChatEngine:
    config = AppConfig()
    config.model = "test-model"
    engine = ChatEngine(config)
    engine._resolve_provider = lambda: provider  # type: ignore[method-assign]
    return engine


def test_retry_honors_retry_after(monkeypatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr("seedcode.core.chat.time.sleep", waits.append)
    provider = _FlakyProvider(failures=1, retry_after=2.0)
    engine = _engine_with(provider)

    assert list(engine.stream_reply()) == ["ok"]
    assert provider.calls == 2
    assert waits == [2.0]  # the provider's hint, not the generic 0.5s backoff


def test_retry_caps_huge_retry_after(monkeypatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr("seedcode.core.chat.time.sleep", waits.append)
    provider = _FlakyProvider(failures=1, retry_after=99999.0)
    engine = _engine_with(provider)

    assert list(engine.stream_reply()) == ["ok"]
    assert waits == [30.0]  # bounded: never sleep for minutes


def test_retry_gives_up_after_max(monkeypatch) -> None:
    monkeypatch.setattr("seedcode.core.chat.time.sleep", lambda _s: None)
    provider = _FlakyProvider(failures=99)
    engine = _engine_with(provider)

    with pytest.raises(ChatError):
        list(engine.stream_reply())
    assert provider.calls == _max_retries() + 1


def test_zero_retries_env_disables_retry(monkeypatch) -> None:
    monkeypatch.setenv("SEEDCODE_MAX_RETRIES", "0")
    provider = _FlakyProvider(failures=1)
    engine = _engine_with(provider)

    with pytest.raises(ChatError):
        list(engine.stream_reply())
    assert provider.calls == 1


def test_max_retries_env_is_clamped(monkeypatch) -> None:
    monkeypatch.setenv("SEEDCODE_MAX_RETRIES", "4")
    assert _max_retries() == 4
    monkeypatch.setenv("SEEDCODE_MAX_RETRIES", "99")
    assert _max_retries() == 5
    monkeypatch.setenv("SEEDCODE_MAX_RETRIES", "-1")
    assert _max_retries() == 0
    monkeypatch.setenv("SEEDCODE_MAX_RETRIES", "banana")
    assert _max_retries() == 2
    monkeypatch.delenv("SEEDCODE_MAX_RETRIES")
    assert _max_retries() == 2


# --- SEEDCODE_PROVIDER / SEEDCODE_MODEL preseeding ---------------------------------


@pytest.fixture()
def _hermetic_env(monkeypatch, tmp_path):
    """Isolated config path, no dotenv, and no stray selection overrides."""
    monkeypatch.setenv("SEEDCODE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("SEEDCODE_PROVIDER", raising=False)
    monkeypatch.delenv("SEEDCODE_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(manager, "config_path", lambda: tmp_path / "config.json")
    return tmp_path


def test_env_selection_seeds_a_fresh_config(_hermetic_env, monkeypatch) -> None:
    monkeypatch.setenv("SEEDCODE_PROVIDER", "ollama")
    monkeypatch.setenv("SEEDCODE_MODEL", "llama3.2")

    cfg = manager.load_config()

    assert cfg.active_provider == "ollama"
    assert cfg.model == "llama3.2"


def test_env_selection_ignores_unknown_provider(_hermetic_env, monkeypatch) -> None:
    monkeypatch.setenv("SEEDCODE_PROVIDER", "not-a-provider")
    monkeypatch.setenv("SEEDCODE_MODEL", "llama3.2")

    cfg = manager.load_config()

    assert cfg.active_provider == defaults.DEFAULT_PROVIDER
    assert cfg.model == "llama3.2"


def test_env_selection_never_overrides_saved_config(_hermetic_env, monkeypatch) -> None:
    path = _hermetic_env / "config.json"
    saved = AppConfig()
    saved.active_provider = "ollama"
    saved.model = "my-fine-tune"
    manager.save_config(saved)

    monkeypatch.setenv("SEEDCODE_PROVIDER", "openrouter")
    monkeypatch.setenv("SEEDCODE_MODEL", "some-default")
    cfg = manager.load_config()

    assert cfg.active_provider == "ollama"
    assert cfg.model == "my-fine-tune"


# --- version output -----------------------------------------------------------------


def test_version_flag_prints_machine_readable_version(monkeypatch, capsys) -> None:
    from seedcode import cli

    monkeypatch.setattr("sys.argv", ["seedcode", "--version"])
    cli.main()
    out = capsys.readouterr().out.strip()
    assert out == f"Seed Code CLI {__import__('seedcode').__version__}"
    assert out.startswith("Seed Code CLI 6.2.5")
