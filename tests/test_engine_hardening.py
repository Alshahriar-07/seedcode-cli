"""Tests for the vNext Computer Engine hardening.

Four workstreams, all exercised against fakes so the suite stays CI-safe:

* the resolver's full strategy ladder (accessibility → UIA → OCR → CV → image
  → relative → absolute),
* DPI / display-scaling resilience,
* default-browser-first web automation (no Selenium),
* the Session Permission Manager that asks once per session.
"""

from __future__ import annotations

import pytest

from seedcode.computer import dpi as dpi_mod
from seedcode.computer.permissions import (
    CATEGORY_APPS,
    CATEGORY_CONTROL,
    CATEGORY_REGISTRY_READ,
    CATEGORY_SECRET,
    SESSION_GRANT_CATEGORIES,
    DesktopGrant,
    DesktopSession,
    SessionPermissionManager,
    session_permissions,
)
from seedcode.computer.resolver import ElementResolver, ResolveError
from seedcode.tools.permissions import PermissionError_


# --- fakes -------------------------------------------------------------------
class _El:
    """Stand-in for vision.UIElement, including the tier-2 UIA properties."""

    def __init__(self, role, name, x, y, enabled=True, width=40, height=20,
                 automation_id="", value="", help_text=""):
        self.role = role
        self.name = name
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.enabled = enabled
        self.automation_id = automation_id
        self.value = value
        self.help_text = help_text


class _Vision:
    """Configurable fake vision driver covering every ladder tier."""

    def __init__(self, elements=(), ocr=None, cv=False, template=None):
        self._elements = list(elements)
        self._ocr = ocr            # box returned by ocr_locate
        self._cv = cv              # whether cv_available()
        self._template = template  # box returned by template_locate
        self.refined = False

    def snapshot(self, window_title=None):
        return ("Test Window", self._elements)

    def ocr_available(self):
        return self._ocr is not None

    def ocr_locate(self, path, phrase):
        return self._ocr

    def cv_available(self):
        return self._cv

    def refine_region_cv(self, path, box):
        self.refined = True
        # Pretend we snapped out to a larger enclosing control.
        left, top, width, height = box
        return (left - 10, top - 5, width + 20, height + 10)

    def template_locate(self, path, template, threshold=0.87):
        return self._template


class _Screen:
    def capture(self, region=None, monitor=None, save_to=None):
        return "C:/fake/shot.png"


# --- resolver ladder ---------------------------------------------------------
class TestResolverLadder:
    def test_accessibility_tier_wins_and_records_trail(self):
        r = ElementResolver(vision=_Vision([_El("button", "Sign in", 100, 50)]), screen=None)
        hit = r.resolve("Sign in button")
        assert hit.source == "accessibility"
        assert hit.tried == ["accessibility"]

    def test_uia_property_tier_matches_automation_id(self):
        """An unnamed input still resolves via its AutomationId."""
        vision = _Vision([_El("input", "", 300, 80, automation_id="searchBox")])
        r = ElementResolver(vision=vision, screen=None)
        hit = r.resolve("searchBox")
        assert hit.source == "uia"
        assert (hit.x, hit.y) == (300, 80)
        assert "accessibility" in hit.tried  # fell through tier 1 first

    def test_uia_tier_matches_help_text(self):
        vision = _Vision([_El("button", "", 10, 10, help_text="Save the document")])
        r = ElementResolver(vision=vision, screen=None)
        assert r.resolve("Save the document").source == "uia"

    def test_ocr_tier_used_when_no_accessibility_tree(self):
        vision = _Vision(elements=[], ocr=(100, 200, 60, 20))
        r = ElementResolver(vision=vision, screen=_Screen())
        hit = r.resolve("Play")
        assert hit.source == "ocr"
        assert (hit.x, hit.y) == (130, 210)  # center of the OCR box

    def test_cv_tier_refines_ocr_box(self):
        """With OpenCV present the click snaps to the enclosing control."""
        vision = _Vision(elements=[], ocr=(100, 200, 60, 20), cv=True)
        r = ElementResolver(vision=vision, screen=_Screen())
        hit = r.resolve("Play")
        assert hit.source == "cv"
        assert vision.refined
        # Center of the refined box (90, 195, 80, 30).
        assert (hit.x, hit.y) == (130, 210)

    def test_image_match_tier_with_template(self):
        vision = _Vision(elements=[], template=(400, 300, 40, 40))
        r = ElementResolver(vision=vision, screen=_Screen())
        hit = r.resolve("the app icon", template="C:/icons/app.png")
        assert hit.source == "image"
        assert (hit.x, hit.y) == (420, 320)

    def test_template_ignored_when_not_supplied(self):
        vision = _Vision(elements=[], template=(400, 300, 40, 40))
        r = ElementResolver(vision=vision, screen=_Screen())
        with pytest.raises(ResolveError):
            r.resolve("the app icon")  # no template -> tier skipped

    def test_absolute_tier_is_internal_only(self):
        r = ElementResolver(vision=_Vision([]), screen=None)
        hit = r.resolve_point(640, 480)
        assert hit.source == "absolute"
        assert (hit.x, hit.y) == (640, 480)

    def test_error_lists_tiers_tried(self):
        r = ElementResolver(vision=_Vision([_El("button", "OK", 5, 5)]), screen=None)
        with pytest.raises(ResolveError, match="tried:"):
            r.resolve("Nonexistent widget")


class TestRelativePositioning:
    def test_below_anchor_offsets_downward(self):
        vision = _Vision([_El("text", "Username", 100, 100, height=20)])
        r = ElementResolver(vision=vision, screen=None)
        hit = r.resolve("the field below Username")
        assert hit.source == "relative"
        assert hit.x == 100
        assert hit.y > 100  # landed beneath the anchor

    def test_right_of_anchor_offsets_sideways(self):
        vision = _Vision([_El("input", "Search", 200, 50, width=100)])
        r = ElementResolver(vision=vision, screen=None)
        hit = r.resolve("the button to the right of Search")
        assert hit.source == "relative"
        assert hit.x > 200
        assert hit.y == 50

    def test_above_anchor_offsets_upward(self):
        vision = _Vision([_El("text", "Total", 80, 400, height=20)])
        r = ElementResolver(vision=vision, screen=None)
        assert r.resolve("the value above Total").y < 400

    def test_relative_beats_fuzzy_match_on_the_anchor(self):
        """The anchor itself must not be returned for a spatial description.

        Without spatial parsing, "the box below Username" fuzzy-matches the
        Username label and clicks the wrong control.
        """
        vision = _Vision([_El("text", "Username", 100, 100, height=20)])
        r = ElementResolver(vision=vision, screen=None)
        hit = r.resolve("the box below Username")
        assert hit.source == "relative"
        assert hit.y != 100  # not the anchor's own point

    def test_unresolvable_anchor_still_raises(self):
        r = ElementResolver(vision=_Vision([]), screen=None)
        with pytest.raises(ResolveError):
            r.resolve("the field below Nonexistent")


# --- DPI resilience ----------------------------------------------------------
class TestDpi:
    def test_ensure_is_idempotent_and_reports_state(self):
        dpi_mod.reset_for_tests()
        first = dpi_mod.ensure_dpi_awareness()
        second = dpi_mod.ensure_dpi_awareness()
        assert first.mode == second.mode
        assert isinstance(first.aware, bool)
        assert first.describe()

    def test_scale_never_returns_zero(self):
        """A unknown/undeterminable DPI must degrade to 1.0, never 0."""
        assert dpi_mod.scale_for_point(0, 0) > 0

    def test_non_windows_is_a_safe_noop(self, monkeypatch):
        dpi_mod.reset_for_tests()
        monkeypatch.setattr(dpi_mod.sys, "platform", "linux")
        info = dpi_mod.ensure_dpi_awareness()
        assert info.mode == "none" and info.aware is False
        dpi_mod.reset_for_tests()


# --- default-browser-first ---------------------------------------------------
class TestDefaultBrowser:
    def test_progid_maps_to_known_families(self, monkeypatch):
        from seedcode.computer import browser

        cases = {
            "ChromeHTML": "chrome",
            "MSEdgeHTM": "edge",
            "FirefoxURL-308046B0AF4A39CB": "firefox",
            "BraveHTML": "brave",
        }
        for prog_id, family in cases.items():
            monkeypatch.setattr(browser, "_win_default_progid", lambda p=prog_id: p)
            monkeypatch.setattr(browser.sys, "platform", "win32")
            assert browser.default_browser().family == family

    def test_unknown_progid_degrades_to_other(self, monkeypatch):
        from seedcode.computer import browser

        monkeypatch.setattr(browser, "_win_default_progid", lambda: "WeirdBrowserHTML")
        monkeypatch.setattr(browser.sys, "platform", "win32")
        assert browser.default_browser().family == "other"

    def test_navigate_uses_os_default_handler_not_selenium(self, monkeypatch):
        """Navigation must go through the OS URL handler, never WebDriver."""
        from seedcode.computer import browser

        opened = []
        monkeypatch.setattr(browser.webbrowser, "open",
                            lambda url, new=0: opened.append((url, new)) or True)
        out = browser.navigate("youtube.com")
        assert opened and opened[0][0] == "https://youtube.com"
        assert browser.current_url() == "https://youtube.com"
        assert "default browser" in out

    def test_navigate_normalizes_and_preserves_schemes(self, monkeypatch):
        from seedcode.computer import browser

        seen = []
        monkeypatch.setattr(browser.webbrowser, "open",
                            lambda url, new=0: seen.append(url) or True)
        browser.navigate("example.com/path")
        browser.navigate("http://plain.test")
        browser.navigate("about:blank")
        assert seen == ["https://example.com/path", "http://plain.test", "about:blank"]

    def test_search_builds_engine_url(self, monkeypatch):
        from seedcode.computer import browser

        seen = []
        monkeypatch.setattr(browser.webbrowser, "open",
                            lambda url, new=0: seen.append(url) or True)
        browser.search("lofi hip hop", engine="youtube")
        assert "youtube.com/results" in seen[0]
        assert "lofi+hip+hop" in seen[0]

    def test_search_requires_a_query(self):
        from seedcode.computer import browser

        with pytest.raises(browser.BrowserError):
            browser.search("   ")

    def test_navigate_failure_raises(self, monkeypatch):
        from seedcode.computer import browser

        monkeypatch.setattr(browser.webbrowser, "open", lambda url, new=0: False)
        monkeypatch.setattr(browser.sys, "platform", "linux")  # no startfile path
        with pytest.raises(browser.BrowserError):
            browser.navigate("example.com")

    def test_is_available_needs_no_selenium(self):
        from seedcode.computer import browser

        ok, _reason = browser.is_available()
        assert ok is True

    def test_dom_helpers_give_actionable_error_without_selenium(self, monkeypatch):
        from seedcode.computer import browser, browser_selenium

        monkeypatch.setattr(browser_selenium, "is_available", lambda: (False, "not installed"))
        with pytest.raises(browser.BrowserError, match="ui_click"):
            browser.click_element("#search")


# --- Session Permission Manager ---------------------------------------------
class TestSessionPermissions:
    def test_grant_covers_all_routine_categories(self):
        session = SessionPermissionManager()
        session.request(SESSION_GRANT_CATEGORIES, allow=True)
        for category in (CATEGORY_CONTROL, CATEGORY_APPS, CATEGORY_REGISTRY_READ):
            assert session.is_granted(category)

    def test_sensitive_is_never_session_granted(self):
        session = SessionPermissionManager()
        session.request([CATEGORY_CONTROL, CATEGORY_SECRET], allow=True)
        assert session.is_granted(CATEGORY_CONTROL)
        assert not session.is_granted(CATEGORY_SECRET)

    def test_granted_session_never_prompts_again(self):
        """The whole point: one grant, zero further asks for routine control."""
        asked = []
        session = SessionPermissionManager()
        session.request(SESSION_GRANT_CATEGORIES, allow=True)
        gate = DesktopSession(
            enabled=True,
            confirm=lambda c, d: asked.append(c) or DesktopGrant.ONCE,
            session=session,
        )
        for _ in range(10):
            gate.check(CATEGORY_CONTROL, "click something")
            gate.check(CATEGORY_APPS, "open notepad")
        assert asked == []  # never interrupted the user

    def test_sensitive_still_asks_every_time_even_when_granted(self):
        asked = []
        session = SessionPermissionManager()
        session.request(SESSION_GRANT_CATEGORIES, allow=True)
        gate = DesktopSession(
            enabled=True,
            confirm=lambda c, d: asked.append(c) or DesktopGrant.ONCE,
            session=session,
        )
        gate.check(CATEGORY_SECRET, "type password")
        gate.check(CATEGORY_SECRET, "type password again")
        assert asked == [CATEGORY_SECRET, CATEGORY_SECRET]

    def test_session_denial_blocks_without_prompting(self):
        asked = []
        session = SessionPermissionManager()
        session.request([CATEGORY_CONTROL], allow=False)
        gate = DesktopSession(
            enabled=True,
            confirm=lambda c, d: asked.append(c) or DesktopGrant.ONCE,
            session=session,
        )
        with pytest.raises(PermissionError_, match="denied"):
            gate.check(CATEGORY_CONTROL, "click")
        assert asked == []

    def test_grant_survives_gate_rebuilds(self):
        """The app rebuilds DesktopSession on level changes; grants must persist."""
        session = SessionPermissionManager()
        session.request(SESSION_GRANT_CATEGORIES, allow=True)
        asked = []
        for _ in range(3):  # simulate three rebuilds
            gate = DesktopSession(
                enabled=True,
                confirm=lambda c, d: asked.append(c) or DesktopGrant.ONCE,
                session=session,
            )
            gate.check(CATEGORY_CONTROL, "click")
        assert asked == []

    def test_reset_makes_it_ask_again(self):
        session = SessionPermissionManager()
        session.request(SESSION_GRANT_CATEGORIES, allow=True)
        session.reset()
        assert not session.requested and not session.granted

    def test_without_session_behaviour_is_unchanged(self):
        """Sessions built without a manager keep the original lazy prompting."""
        asked = []
        gate = DesktopSession(
            enabled=True, confirm=lambda c, d: asked.append(c) or DesktopGrant.ONCE
        )
        gate.check(CATEGORY_CONTROL, "click 1")
        gate.check(CATEGORY_CONTROL, "click 2")
        assert len(asked) == 2

    def test_disabled_gate_still_blocks_even_when_granted(self):
        session = SessionPermissionManager()
        session.request(SESSION_GRANT_CATEGORIES, allow=True)
        gate = DesktopSession(enabled=False, session=session)
        with pytest.raises(PermissionError_, match="desktop mode is off"):
            gate.check(CATEGORY_CONTROL, "click")

    def test_singleton_is_shared(self):
        assert session_permissions() is session_permissions()


class TestAssistWiring:
    """/assist on must ask once; /assist off must forget."""

    class _UI:
        def __init__(self, answer="a"):
            self.answer = answer
            self.prompts = []
            self.messages = []

        def confirm_desktop(self, label, description):
            self.prompts.append((label, description))
            return self.answer

        def dim(self, msg):
            self.messages.append(msg)

        def success(self, msg):
            self.messages.append(msg)

        def blank(self):
            pass

        def panel(self, *a, **k):
            pass

        def warning(self, msg):
            self.messages.append(msg)

    @pytest.fixture(autouse=True)
    def _clean_session(self):
        session_permissions().reset()
        yield
        session_permissions().reset()

    def test_allow_grants_whole_session_in_one_prompt(self):
        from seedcode.commands.assist import request_session_permissions

        ui = self._UI(answer="a")
        assert request_session_permissions(ui) is True
        assert len(ui.prompts) == 1
        assert session_permissions().is_granted(CATEGORY_CONTROL)

    def test_prompt_is_not_repeated_once_answered(self):
        from seedcode.commands.assist import request_session_permissions

        ui = self._UI(answer="a")
        request_session_permissions(ui)
        request_session_permissions(ui)
        request_session_permissions(ui)
        assert len(ui.prompts) == 1  # asked exactly once per session

    def test_decline_falls_back_to_per_action_not_a_hard_block(self):
        """Declining must not brick Assist: nothing is granted *or* denied."""
        from seedcode.commands.assist import request_session_permissions

        ui = self._UI(answer="n")
        assert request_session_permissions(ui) is False
        session = session_permissions()
        assert not session.is_granted(CATEGORY_CONTROL)
        assert not session.is_denied(CATEGORY_CONTROL)

    def test_headless_prompt_failure_grants_nothing(self):
        from seedcode.commands.assist import request_session_permissions

        class _Broken(self._UI):
            def confirm_desktop(self, label, description):
                raise RuntimeError("no tty")

        assert request_session_permissions(_Broken()) is False
        assert not session_permissions().granted

    def test_disable_assist_resets_the_session_grant(self, tmp_path, monkeypatch):
        import seedcode.commands.assist as assist_mod
        from seedcode.core.models import AppConfig

        monkeypatch.setattr(assist_mod, "save_config", lambda c: None)
        session_permissions().request(SESSION_GRANT_CATEGORIES, allow=True)
        assist_mod.disable_assist(self._UI(), AppConfig())
        assert not session_permissions().granted
        assert not session_permissions().requested
