"""Tests for the Browser Engine, Popup Manager, and browser skills.

These cover the architectural contract the browser refactor exists to enforce:

* a browser goal is reached by URL, not by clicking — so a workflow needs no
  OCR, no screenshots, and no AI turns in the middle;
* popups are the engine's problem and are cleared automatically;
* every action is verified before it is reported as done;
* ``ui_*`` verbs are refused on browser windows so the old click-by-click
  pattern cannot come back.

Everything here is hermetic: no browser is launched and no URL is fetched.
"""

from __future__ import annotations

import pytest

from seedcode.computer.browser_engine import (
    BrowserEngine,
    BrowserWorkflowError,
    _same_page,
)
from seedcode.computer.browser_popups import PopupManager, PopupRule, SweepResult


# --- fakes -------------------------------------------------------------------

class _FakeDriver:
    """Stands in for the OS default-browser driver."""

    def __init__(self, fail: bool = False):
        self.calls = []
        self.url = None
        self._fail = fail

    def navigate(self, url, new_window=False):
        if self._fail:
            raise RuntimeError("no handler")
        self.calls.append(url)
        self.url = url
        return f"Opened {url}"

    def current_url(self):
        return self.url

    def default_browser(self):
        return type("B", (), {"name": "Test Browser", "describe": lambda s: "Test Browser"})()


class _FakeCdp:
    """A scriptable DevTools stand-in."""

    def __init__(self, available=False, url=None, title=None, tabs=(), found=()):
        self._available = available
        self.url = url
        self.title = title
        self._tabs = list(tabs)
        # Selectors/texts that "exist" on the page.
        self.found = set(found)
        self.clicked = []
        self.evaluated = []
        self.closed = []
        self.activated = []
        # Optional side effect run after a successful click, so a fake can
        # model "clicking this link navigates the tab".
        self.on_click = None

    def is_available(self, port=None):
        return self._available

    def current_url(self, port=None):
        return self.url

    def current_title(self, port=None):
        return self.title

    def navigate(self, url, port=None):
        self.url = url
        return True

    def wait_for_load(self, timeout_s=0, port=None):
        return True

    def evaluate(self, expression, port=None):
        self.evaluated.append(expression)
        return True

    def exists(self, selector, port=None):
        return selector in self.found

    def click_text(self, phrase, port=None):
        if phrase in self.found:
            self.clicked.append(phrase)
            self.found.discard(phrase)
            return True
        return False

    def click_selector(self, selector, port=None):
        if selector in self.found:
            self.clicked.append(selector)
            self.found.discard(selector)
            if self.on_click is not None:
                self.on_click()
            return True
        return False

    def list_tabs(self, port=None):
        return list(self._tabs)

    def active_tab(self, port=None):
        return self._tabs[0] if self._tabs else None

    def new_tab(self, url="about:blank", port=None):
        return None

    def close_tab(self, target_id, port=None):
        self.closed.append(target_id)
        return True

    def activate_tab(self, target_id, port=None):
        self.activated.append(target_id)
        return True


class _FakeController:
    def __init__(self):
        self.hotkeys = []
        self.clicks = []
        self.focused = []
        self.vision = None

    def hotkey(self, keys):
        self.hotkeys.append(tuple(keys))

    def mouse_click(self, x, y, button="left", double=False):
        self.clicks.append((x, y))
        return "clicked"

    def focus_window(self, title):
        self.focused.append(title)
        return "focused"


class _NoPopups:
    def sweep(self, max_passes=2):
        return SweepResult()


def _tab(tid, title, url):
    return type("T", (), {
        "target_id": tid, "title": title, "url": url, "ws_url": "",
        "describe": lambda self: f'"{title}" — {url}',
    })()


def _engine(**kw):
    """A BrowserEngine wired for offline, instant tests."""
    kw.setdefault("controller", _FakeController())
    kw.setdefault("driver", _FakeDriver())
    kw.setdefault("cdp", _FakeCdp())
    kw.setdefault("popups", _NoPopups())
    kw.setdefault("settle_s", 0.0)
    kw.setdefault("fetch", lambda url: None)
    return BrowserEngine(**kw)


# --- search is a URL, never a click -----------------------------------------

class TestSearchIsAUrl:
    def test_youtube_search_builds_a_results_url(self):
        driver = _FakeDriver()
        out = _engine(driver=driver).youtube_search("lofi beats")
        assert driver.calls == ["https://www.youtube.com/results?search_query=lofi+beats"]
        assert "lofi beats" in out.detail

    def test_google_search_builds_a_results_url(self):
        driver = _FakeDriver()
        _engine(driver=driver).google_search("python docs")
        assert "google.com/search?q=python+docs" in driver.calls[0]

    def test_search_needs_no_mouse_or_keyboard(self):
        """The whole point: no clicking, no typing, no OCR to run a search."""
        controller = _FakeController()
        driver = _FakeDriver()
        _engine(controller=controller, driver=driver).youtube_search("anything")
        assert controller.clicks == []
        assert controller.hotkeys == []

    def test_search_rejects_an_empty_query(self):
        with pytest.raises(BrowserWorkflowError, match="query is required"):
            _engine().search("   ")

    def test_search_rejects_an_unknown_engine(self):
        with pytest.raises(BrowserWorkflowError, match="Unknown search engine"):
            _engine().search("x", engine="askjeeves")

    def test_navigation_failure_is_reported(self):
        with pytest.raises(BrowserWorkflowError, match="Could not open"):
            _engine(driver=_FakeDriver(fail=True)).open_url("example.com")


# --- youtube_play: the workflow that used to need OCR ------------------------

class TestYoutubePlay:
    _PAYLOAD = '{"videoId":"dQw4w9WgXcQ","title":{"runs":[{"text":"Love Me Thoda Aur"}]}}'

    def test_resolves_a_video_id_over_http_and_plays_it(self):
        """No search page, no thumbnail, no click — one navigation."""
        driver = _FakeDriver()
        out = _engine(driver=driver, fetch=lambda url: self._PAYLOAD).youtube_play(
            "Love Me Thoda Aur"
        )
        assert driver.calls == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&autoplay=1"]
        assert "Love Me Thoda Aur" in out.detail
        assert out.expected == {"browser_url": "watch?v=dQw4w9WgXcQ"}

    def test_play_needs_no_mouse_input(self):
        controller = _FakeController()
        _engine(controller=controller, fetch=lambda url: self._PAYLOAD).youtube_play("x")
        assert controller.clicks == []

    def test_falls_back_to_clicking_the_first_result_in_the_dom(self):
        """When the lookup fails, the DOM tier picks the result — not the AI."""
        selector = "a#video-title, ytd-video-renderer a#thumbnail"
        cdp = _FakeCdp(available=True, found=[selector])
        # Clicking a result navigates the tab, exactly as the real page does.
        cdp.on_click = lambda: setattr(cdp, "url", "https://www.youtube.com/watch?v=abc")
        out = _engine(cdp=cdp, fetch=lambda url: None).youtube_play("some song")
        assert selector in cdp.clicked
        assert "playing" in out.detail

    def test_reports_failure_when_no_video_can_be_reached(self):
        """A failed workflow must fail loudly, not report a half-done state."""
        with pytest.raises(BrowserWorkflowError, match="Could not resolve a video"):
            _engine(cdp=_FakeCdp(available=False), fetch=lambda url: None).youtube_play("x")

    def test_reports_failure_when_the_click_opens_no_watch_page(self):
        selector = "a#video-title, ytd-video-renderer a#thumbnail"
        cdp = _FakeCdp(available=True, url="https://www.youtube.com/results?search_query=x",
                       found=[selector])
        with pytest.raises(BrowserWorkflowError, match="no watch page opened"):
            _engine(cdp=cdp, fetch=lambda url: None).youtube_play("x")

    def test_requires_a_query(self):
        with pytest.raises(BrowserWorkflowError):
            _engine().youtube_play("")


# --- verification ------------------------------------------------------------

class TestVerification:
    def test_landing_on_the_wrong_page_is_a_failure(self):
        """Never report success the browser did not actually reach."""
        cdp = _FakeCdp(available=True, url="https://example.org/elsewhere")
        engine = _engine(cdp=cdp)
        # The fake CDP records the requested URL, so force a divergent answer.
        cdp.navigate = lambda url, port=None: True
        with pytest.raises(BrowserWorkflowError, match="did not land"):
            engine.open_url("https://example.com/wanted")

    def test_matching_page_verifies(self):
        cdp = _FakeCdp(available=True, url="https://www.example.com/wanted?utm=1")
        cdp.navigate = lambda url, port=None: True
        out = _engine(cdp=cdp).open_url("https://example.com/wanted")
        assert "opened" in out.detail

    @pytest.mark.parametrize(
        "live,wanted,expected",
        [
            ("https://www.youtube.com/watch?v=abc", "https://youtube.com/watch?v=abc", True),
            ("https://www.youtube.com/watch?v=zzz", "https://youtube.com/watch?v=abc", False),
            ("https://example.com/a?utm_source=x", "https://example.com/a", True),
            ("https://evil.com/a", "https://example.com/a", False),
        ],
    )
    def test_same_page_tolerates_noise_but_not_the_wrong_video(self, live, wanted, expected):
        assert _same_page(live, wanted) is expected


# --- tabs and history --------------------------------------------------------

class TestTabsAndHistory:
    def test_close_tab_uses_devtools_when_available(self):
        cdp = _FakeCdp(available=True, tabs=[_tab("t1", "YouTube", "https://youtube.com")])
        out = _engine(cdp=cdp).close_tab()
        assert cdp.closed == ["t1"]
        assert "closed" in out.detail

    def test_close_tab_falls_back_to_the_keyboard(self):
        controller = _FakeController()
        _engine(controller=controller, cdp=_FakeCdp(available=False)).close_tab()
        assert ("ctrl", "w") in controller.hotkeys

    def test_switch_tab_matches_on_title(self):
        tabs = [_tab("t1", "YouTube", "https://youtube.com"),
                _tab("t2", "Docs", "https://docs.python.org")]
        cdp = _FakeCdp(available=True, tabs=tabs)
        _engine(cdp=cdp).switch_tab("docs")
        assert cdp.activated == ["t2"]

    def test_switch_tab_reports_when_nothing_matches(self):
        tabs = [_tab("t1", "YouTube", "https://youtube.com")]
        with pytest.raises(BrowserWorkflowError, match="No open tab matches"):
            _engine(cdp=_FakeCdp(available=True, tabs=tabs)).switch_tab("spreadsheet")

    def test_back_uses_history_and_detects_no_movement(self):
        """Going back on the first page must not be reported as success."""
        cdp = _FakeCdp(available=True, url="https://example.com/only")
        with pytest.raises(BrowserWorkflowError, match="did not change"):
            _engine(cdp=cdp).back()

    def test_back_falls_back_to_alt_left(self):
        controller = _FakeController()
        driver = _FakeDriver()
        driver.url = None
        _engine(controller=controller, cdp=_FakeCdp(available=False), driver=driver).back()
        assert ("alt", "left") in controller.hotkeys

    def test_keyboard_back_is_not_falsely_reported_as_failed(self):
        """Without DevTools nothing can observe the move, so don't guess.

        The driver's "last URL we opened" never changes when the user goes
        back; treating it as evidence would fail every successful keyboard
        navigation.
        """
        driver = _FakeDriver()
        driver.url = "https://example.com/page"
        out = _engine(cdp=_FakeCdp(available=False), driver=driver).back()
        assert "went back" in out.detail

    def test_refresh_reloads(self):
        controller = _FakeController()
        _engine(controller=controller, cdp=_FakeCdp(available=False)).refresh()
        assert ("f5",) in controller.hotkeys


# --- popup manager -----------------------------------------------------------

class _El:
    def __init__(self, name, x=10, y=20):
        self.name, self.x, self.y = name, x, y


class _VisionWith:
    def __init__(self, *names):
        self._els = [_El(n) for n in names]

    def snapshot(self, window_title=None):
        return ("Chrome", self._els)


class TestPopupManager:
    def test_clean_page_dismisses_nothing(self):
        result = PopupManager(cdp=_FakeCdp(available=True), controller=_FakeController()).sweep()
        assert not result
        assert result.describe() == "no popups present"

    def test_cookie_banner_is_accepted_automatically(self):
        cdp = _FakeCdp(available=True, found=["#onetrust-banner-sdk", "accept all"])
        result = PopupManager(cdp=cdp, controller=_FakeController()).sweep()
        assert "cookies" in result.dismissed
        assert "accept all" in cdp.clicked

    def test_stacked_popups_are_cleared_in_one_sweep(self):
        """Dismissing one banner often reveals the next; the sweep handles it."""
        cdp = _FakeCdp(
            available=True,
            found=["#onetrust-banner-sdk", "accept all",
                   "#credential_picker_container", "no thanks"],
        )
        result = PopupManager(cdp=cdp, controller=_FakeController()).sweep()
        assert {"cookies", "signin"} <= set(result.dismissed)

    def test_never_clicks_sign_in_while_dismissing(self):
        """Clearing a popup must never start an auth flow on the user's behalf."""
        rule = PopupRule(
            kind="signin", label="test",
            selectors=("#modal",), accept_text=("sign in", "no thanks"),
        )
        cdp = _FakeCdp(available=True, found=["#modal", "sign in", "no thanks"])
        PopupManager(cdp=cdp, controller=_FakeController(), rules=[rule]).sweep()
        assert "sign in" not in cdp.clicked
        assert "no thanks" in cdp.clicked

    def test_never_clicks_allow_on_a_permission_prompt(self):
        rule = PopupRule(
            kind="notifications", label="test",
            selectors=("#push",), accept_text=("allow", "block"),
        )
        cdp = _FakeCdp(available=True, found=["#push", "allow", "block"])
        PopupManager(cdp=cdp, controller=_FakeController(), rules=[rule]).sweep()
        assert "allow" not in cdp.clicked
        assert "block" in cdp.clicked

    def test_translate_bar_is_found_through_the_accessibility_tree(self):
        """Browser-chrome popups have no DOM, so UIA is the detection surface."""
        controller = _FakeController()
        controller.vision = _VisionWith("Translate this page?", "Nope")
        manager = PopupManager(cdp=_FakeCdp(available=False), controller=controller)
        assert "translate" in manager.present()

    def test_dont_allow_is_clickable_even_though_allow_is_not(self):
        """A negated caption is the decline control and must stay usable.

        Regression: substring matching treated "Don't allow" as forbidden
        because it contains "allow", so a notification prompt offering only
        that button was never cleared.
        """
        rule = PopupRule(
            kind="notifications", label="test",
            selectors=("#push",), accept_text=("don't allow",),
        )
        cdp = _FakeCdp(available=True, found=["#push", "don't allow"])
        result = PopupManager(cdp=cdp, controller=None, rules=[rule]).sweep()
        assert "don't allow" in cdp.clicked
        assert "notifications" in result.dismissed

    @pytest.mark.parametrize("caption", ["allow", "allow all", "sign in", "subscribe"])
    def test_affirmative_captions_stay_blocked(self, caption):
        from seedcode.computer.browser_popups import _forbidden

        assert _forbidden(caption) is True

    @pytest.mark.parametrize(
        "caption",
        ["don't allow", "block", "no thanks", "reject all", "never translate",
         "stay signed out", "not now"],
    )
    def test_declining_captions_are_permitted(self, caption):
        from seedcode.computer.browser_popups import _forbidden

        assert _forbidden(caption) is False

    def test_every_shipped_caption_is_actually_clickable(self):
        """A rule listing a caption the safety filter blocks is dead config."""
        from seedcode.computer.browser_popups import RULES, _forbidden

        dead = [(r.kind, c) for r in RULES for c in r.accept_text if _forbidden(c)]
        assert dead == [], f"unreachable dismiss captions: {dead}"

    def test_escape_only_counts_when_the_popup_really_went_away(self):
        """Esc must not be logged as a dismissal if the banner is still up."""
        class _Stubborn(_FakeCdp):
            def click_text(self, phrase, port=None):
                return False

            def click_selector(self, selector, port=None):
                return False

        cdp = _Stubborn(available=True, found=["#onetrust-banner-sdk"])
        controller = _FakeController()
        result = PopupManager(cdp=cdp, controller=controller).sweep()
        assert result.dismissed == []
        assert ("esc",) in controller.hotkeys  # it tried

    def test_a_sweep_never_raises_when_drivers_fail(self):
        class _Broken:
            def is_available(self, port=None):
                raise RuntimeError("devtools exploded")

            def exists(self, selector, port=None):
                raise RuntimeError("devtools exploded")

            def click_text(self, phrase, port=None):
                raise RuntimeError("devtools exploded")

            def click_selector(self, selector, port=None):
                raise RuntimeError("devtools exploded")

        assert PopupManager(cdp=_Broken(), controller=None).sweep().dismissed == []

    def test_popups_cleared_during_a_workflow_are_noted_not_escalated(self):
        """The AI is told the outcome, never asked to deal with a popup."""
        class _OnePopup:
            def __init__(self):
                self.swept = 0

            def sweep(self, max_passes=2):
                self.swept += 1
                return SweepResult(dismissed=["cookies"])

        popups = _OnePopup()
        out = _engine(popups=popups).youtube_search("x")
        assert popups.swept == 1
        assert "cleared cookies" in out.detail
