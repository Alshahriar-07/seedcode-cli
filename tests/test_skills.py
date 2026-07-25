"""Tests for the skill engine and built-in catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from seedcode.computer import browser_skills
from seedcode.computer import catalog  # noqa: F401 — populates REGISTRY
from seedcode.computer.browser_engine import BrowserEngine
from seedcode.computer.skills import (
    Outcome,
    REGISTRY,
    Skill,
    SkillContext,
    SkillError,
    SkillRegistry,
)
from seedcode.tools.permissions import PermissionLevel, PermissionManager


class _FakeBrowserDriver:
    """Stands in for ``seedcode.computer.browser``.

    Browser skills reach the outside world only through the controller's
    ``browser`` attribute, so a controller carrying one of these keeps the
    whole browser catalog offline: the suite never opens a tab.
    """

    def __init__(self):
        self.calls = []
        self.url = None

    def navigate(self, url, new_window=False):
        self.calls.append(("nav", url))
        self.url = url
        return f"Opened {url} in Test Browser (default browser)."

    def current_url(self):
        return self.url

    def default_browser(self):
        return type(
            "B", (), {"name": "Test Browser", "describe": lambda self: "Test Browser"}
        )()


class _FakeController:
    def __init__(self):
        self.calls = []
        self._windows = []
        self.browser = _FakeBrowserDriver()

    def list_windows(self):
        return list(self._windows)

    def focus_window(self, title):
        self.calls.append(("focus", title))
        return type("W", (), {"title": title})()

    def open_app(self, target):
        self.calls.append(("open", target))

    def close_app(self, target, force=False):
        self.calls.append(("close", target))

    def browser_navigate(self, url):
        self.calls.append(("nav", url))
        return f"Opened {url} in Test Browser (default browser)."

    def browser_search(self, query, engine="google"):
        self.calls.append(("search", engine, query))
        return f"Opened {engine} search for '{query}' in Test Browser."

    def browser_default(self):
        return "Test Browser"

    def hotkey(self, keys):
        self.calls.append(("hotkey", tuple(keys)))

    def type_text(self, text):
        self.calls.append(("type", text))

    def mouse_click(self, x, y, button="left", double=False):
        self.calls.append(("click", x, y))


class _FakeState:
    def __init__(self):
        self.actions = []
        self.focus = None
        self.browser_url = None
        self.task = None

    def record_action(self, d):
        self.actions.append(d)

    def note_focus(self, t):
        self.focus = t

    def set_browser_url(self, url):
        self.browser_url = url

    def set_task(self, task):
        self.task = task


class _NoCdp:
    """A DevTools provider that is never available (the common machine)."""

    def is_available(self, port=None):
        return False


class _NoPopups:
    """A popup manager that finds nothing — the clean-page case.

    Browser skills build their own PopupManager, which probes DevTools and the
    accessibility tree. Injecting this keeps skill tests hermetic and fast;
    real sweep behaviour is covered in ``test_browser_popups.py``.
    """

    def sweep(self, max_passes=2):
        from seedcode.computer.browser_popups import SweepResult

        return SweepResult()


def _ctx(level=PermissionLevel.DESKTOP, workspace=None):
    perms = PermissionManager(workspace=workspace or Path.cwd(), level=level)
    controller = _FakeController()
    # Browser skills cache a BrowserEngine per controller id; ids get recycled,
    # so clear the cache to guarantee each test gets an engine bound to ITS
    # fake controller rather than a dead one from a previous test.
    browser_skills.reset_engines()
    # Pre-seed the cache with an engine that touches neither the network, the
    # clock, nor DevTools.
    browser_skills.bind_engine(
        controller,
        BrowserEngine(
            controller=controller,
            cdp=_NoCdp(),
            popups=_NoPopups(),
            settle_s=0.0,
            fetch=lambda url: None,
        ),
    )
    return SkillContext(
        controller=controller,
        resolver=None,
        state=_FakeState(),
        permissions=perms,
    )


# --- engine -----------------------------------------------------------------

def test_registry_lookup_case_insensitive():
    assert REGISTRY.get("LAUNCH_APP") is not None
    assert REGISTRY.get("launch_app") is REGISTRY.get("LAUNCH_APP")


def test_skill_enforces_permission_floor():
    ctx = _ctx(level=PermissionLevel.WORKSPACE)  # below DESKTOP
    launch = REGISTRY.get("launch_app")
    from seedcode.tools.permissions import PermissionError_

    with pytest.raises(PermissionError_):
        launch.run(ctx, {"target": "notepad"})


def test_manifest_hides_skills_above_level():
    ro = REGISTRY.manifest(max_level=PermissionLevel.READ_ONLY)
    assert "launch_app" not in ro  # DESKTOP skill hidden at read-only
    desktop = REGISTRY.manifest(max_level=PermissionLevel.DESKTOP)
    assert "launch_app" in desktop


def test_custom_registry_isolated():
    reg = SkillRegistry()
    reg.register(Skill("noop", "does nothing", PermissionLevel.READ_ONLY,
                       lambda c, p: Outcome("ok")))
    assert reg.get("noop") is not None
    assert reg.get("launch_app") is None  # separate from the global REGISTRY


# --- catalog behaviour ------------------------------------------------------

def test_launch_app_focuses_existing_window():
    ctx = _ctx()
    ctx.controller._windows = [type("W", (), {"title": "Untitled - Notepad"})()]
    out = REGISTRY.get("launch_app").run(ctx, {"target": "notepad"})
    assert ("focus", "Untitled - Notepad") in ctx.controller.calls
    assert ("open", "notepad") not in ctx.controller.calls
    assert out.expected == {"window": "notepad"}


def test_launch_app_opens_when_absent():
    ctx = _ctx()
    REGISTRY.get("launch_app").run(ctx, {"target": "notepad"})
    assert ("open", "notepad") in ctx.controller.calls


def test_youtube_search_navigates_to_a_results_url():
    """Searching is a URL, not a click: no search box is ever touched.

    The skill delegates to the BrowserEngine, which builds the results address
    and drives the default browser to it. Popup handling and verification are
    the engine's job and are covered in ``test_browser_engine.py``.
    """
    ctx = _ctx()
    REGISTRY.get("youtube_search").run(ctx, {"query": "lofi beats"})
    (kind, url), = ctx.controller.browser.calls
    assert kind == "nav"
    assert "youtube.com/results" in url
    assert "lofi+beats" in url
    # No mouse or keyboard input was needed to run a search.
    assert ctx.controller.calls == []


def test_web_search_honours_engine_choice():
    ctx = _ctx()
    REGISTRY.get("web_search").run(ctx, {"query": "python docs", "engine": "duckduckgo"})
    (_kind, url), = ctx.controller.browser.calls
    assert "duckduckgo.com" in url
    assert "python+docs" in url


def test_web_search_defaults_to_google():
    ctx = _ctx()
    REGISTRY.get("web_search").run(ctx, {"query": "python docs"})
    (_kind, url), = ctx.controller.browser.calls
    assert "google.com/search" in url


def test_web_search_rejects_an_unknown_engine():
    ctx = _ctx()
    with pytest.raises(SkillError, match="Unknown search engine"):
        REGISTRY.get("web_search").run(ctx, {"query": "x", "engine": "askjeeves"})


def test_launch_browser_uses_default_browser():
    ctx = _ctx()
    out = REGISTRY.get("launch_browser").run(ctx, {"url": "youtube.com"})
    assert ("nav", "https://youtube.com") in ctx.controller.browser.calls
    assert out.expected == {"browser_url": "youtube.com"}


def test_open_url_normalizes_a_bare_host():
    ctx = _ctx()
    out = REGISTRY.get("open_url").run(ctx, {"url": "example.com/path"})
    assert ("nav", "https://example.com/path") in ctx.controller.browser.calls
    assert out.expected == {"browser_url": "example.com"}


def test_open_url_requires_a_url():
    ctx = _ctx()
    with pytest.raises(SkillError):
        REGISTRY.get("open_url").run(ctx, {})


def test_browser_navigation_skills_are_registered():
    """The catalog must cover whole workflows, so the AI never clicks."""
    for name in (
        "youtube_search", "youtube_play", "google_search", "open_url",
        "close_tab", "new_tab", "switch_tab", "back", "forward", "refresh",
    ):
        assert REGISTRY.get(name) is not None, f"missing browser skill: {name}"


def test_launch_browser_blank_tab_has_no_url_expectation():
    ctx = _ctx()
    out = REGISTRY.get("launch_browser").run(ctx, {})
    assert out.expected is None  # nothing to verify for about:blank


def test_missing_required_param_raises():
    ctx = _ctx()
    with pytest.raises(SkillError):
        REGISTRY.get("google_search").run(ctx, {})


def test_create_python_project_writes_files(tmp_path):
    ctx = _ctx(level=PermissionLevel.WORKSPACE, workspace=tmp_path)
    out = REGISTRY.get("create_python_project").run(ctx, {"name": "demo"})
    main = tmp_path / "demo" / "src" / "main.py"
    assert main.is_file()
    assert "Hello from demo" in main.read_text()
    assert out.expected == {"file_exists": str(main)}


def test_save_current_file_sends_ctrl_s():
    ctx = _ctx()
    REGISTRY.get("save_current_file").run(ctx, {})
    assert ("hotkey", ("ctrl", "s")) in ctx.controller.calls
