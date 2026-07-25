"""Tests for the browser architectural boundary.

Two guarantees the refactor rests on, both enforced in code rather than by
prompt wording:

* the AI cannot click its way around a web page — mutating ``ui_*`` verbs are
  refused on browser windows and redirected to the skill that does the whole
  job;
* OCR is provisioned by the installer, and its availability is reported
  honestly (the engine binary, not just the Python wrapper).
"""

from __future__ import annotations

import pytest

from seedcode.computer.dispatcher import SkillDispatcher, _is_browser_window
from seedcode.computer.resolver import ElementResolver, ResolveError
from seedcode.computer.skills import REGISTRY
from seedcode.computer.state import StateManager
from seedcode.computer.verifier import VerificationEngine
from seedcode.tools.permissions import PermissionLevel, PermissionManager


# --- fakes -------------------------------------------------------------------

class _Win:
    def __init__(self, title):
        self.title = title

    def describe(self):
        return self.title


class _FakeWindows:
    def __init__(self, active=None, titles=()):
        self._active = active
        self.titles = list(titles)

    def active_window(self):
        return self._active

    def list_windows(self):
        return [_Win(t) for t in self.titles]


class _El:
    def __init__(self, role, name, x=5, y=6, enabled=True):
        self.role, self.name, self.x, self.y = role, name, x, y
        self.width = self.height = 10
        self.enabled = enabled
        self.automation_id = self.value = self.help_text = ""


class _FakeVision:
    def __init__(self, elements=()):
        self._elements = list(elements)

    def snapshot(self, window_title=None):
        return ("Window", list(self._elements))

    def ocr_available(self):
        return False

    def element_at(self, x, y):
        return "element"


class _FakeController:
    def __init__(self, active_title=None, elements=()):
        self.windows = _FakeWindows(active=_Win(active_title) if active_title else None)
        self.vision = _FakeVision(elements)
        self.mouse = None
        self.clicks = []

    def mouse_click(self, x, y, button="left", double=False):
        self.clicks.append((x, y))
        return "clicked"

    def wait(self, seconds):
        return "waited"

    def hotkey(self, keys):
        return "hotkey"

    def see(self, title=None):
        return "snapshot"


def _dispatcher(controller, tmp_path):
    perms = PermissionManager(workspace=tmp_path, level=PermissionLevel.DESKTOP)
    return SkillDispatcher(
        controller=controller,
        resolver=ElementResolver(vision=controller.vision, screen=None, dom=None),
        state=StateManager(windows=controller.windows, mouse=None),
        permissions=perms,
        registry=REGISTRY,
        verifier=VerificationEngine(
            vision=controller.vision, windows=controller.windows,
            timeout_s=0.0, poll_s=0.0,
        ),
    )


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    """Keep the execution log out of the real ~/.seedcode."""
    monkeypatch.setattr("seedcode.utils.helpers.app_dir", lambda: tmp_path)


# --- the ui_* block ----------------------------------------------------------

class TestBrowserWindowDetection:
    @pytest.mark.parametrize("title", [
        "YouTube - Google Chrome",
        "Inbox — Mozilla Firefox",
        "Docs and more - Microsoft Edge",
        "Search - Brave",
        "news - Opera",
    ])
    def test_browser_titles_are_recognised(self, title):
        assert _is_browser_window(title) is True

    @pytest.mark.parametrize("title", [
        "Untitled - Notepad", "main.py - Visual Studio Code", "", None,
    ])
    def test_native_app_titles_are_not(self, title):
        assert _is_browser_window(title) is False


class TestUiActionsRefusedOnBrowsers:
    @pytest.mark.parametrize(
        "verb", ["ui_click", "ui_double_click", "ui_right_click", "ui_type"]
    )
    def test_mutating_verbs_are_refused(self, verb, tmp_path):
        controller = _FakeController(active_title="YouTube - Google Chrome")
        result = _dispatcher(controller, tmp_path).dispatch(
            verb, {"target": "the first video", "text": "x"}
        )
        assert result.ok is False
        assert "not available on browser windows" in result.detail
        # And nothing was actually clicked.
        assert controller.clicks == []

    def test_refusal_names_the_skills_to_use_instead(self, tmp_path):
        controller = _FakeController(active_title="YouTube - Google Chrome")
        result = _dispatcher(controller, tmp_path).dispatch(
            "ui_click", {"target": "the first video"}
        )
        assert "youtube_play" in result.detail
        assert "computer_run" in result.detail
        # The replan hint carries the same guidance the AI must act on.
        assert result.replan_hint and "youtube_play" in result.replan_hint

    def test_observation_verbs_still_work_on_browsers(self, tmp_path):
        """ui_assert/ui_wait_for read the page; they are not clicking."""
        controller = _FakeController(
            active_title="YouTube - Google Chrome",
            elements=[_El("text", "Love Me Thoda Aur")],
        )
        result = _dispatcher(controller, tmp_path).dispatch(
            "ui_assert", {"target": "Love Me Thoda Aur"}
        )
        assert result.ok is True

    def test_native_windows_are_unaffected(self, tmp_path):
        """The guard must not break ordinary desktop automation."""
        controller = _FakeController(
            active_title="Untitled - Notepad", elements=[_El("button", "Save")]
        )
        result = _dispatcher(controller, tmp_path).dispatch("ui_click", {"target": "Save"})
        assert result.ok is True
        assert controller.clicks == [(5, 6)]

    def test_guard_fails_open_when_the_window_cannot_be_read(self, tmp_path):
        """An unreadable foreground window must not block native automation."""
        controller = _FakeController(active_title=None, elements=[_El("button", "Save")])
        result = _dispatcher(controller, tmp_path).dispatch("ui_click", {"target": "Save"})
        assert result.ok is True


# --- the DOM tier ------------------------------------------------------------

class _FakeDom:
    def __init__(self, available=True, box=None):
        self._available = available
        self._box = box
        self.queries = []

    def is_available(self, port=None):
        return self._available

    def locate_text(self, phrase, port=None):
        self.queries.append(phrase)
        return self._box


class _Box:
    def __init__(self, x, y, text=""):
        self.x, self.y, self.text = x, y, text
        self.width = self.height = 20


class TestDomTier:
    def test_dom_resolves_when_accessibility_finds_nothing(self):
        dom = _FakeDom(box=_Box(300, 400, "Accept all"))
        hit = ElementResolver(vision=_FakeVision(), screen=None, dom=dom).resolve("Accept all")
        assert (hit.x, hit.y) == (300, 400)
        assert hit.source == "dom"
        assert hit.tried == ["accessibility", "uia", "dom"]

    def test_accessibility_still_wins_over_dom(self):
        """The ladder order matters: exact UIA beats a DOM text match."""
        dom = _FakeDom(box=_Box(999, 999))
        resolver = ElementResolver(
            vision=_FakeVision([_El("button", "Save")]), screen=None, dom=dom
        )
        hit = resolver.resolve("Save button")
        assert hit.source == "accessibility"
        assert dom.queries == []  # the DOM was never consulted

    def test_dom_is_skipped_when_no_browser_is_attached(self):
        dom = _FakeDom(available=False)
        with pytest.raises(ResolveError) as exc:
            ElementResolver(vision=_FakeVision(), screen=None, dom=dom).resolve("anything")
        assert "dom" in str(exc.value)
        assert dom.queries == []

    def test_a_broken_dom_provider_falls_through(self):
        class _Broken:
            def is_available(self, port=None):
                raise RuntimeError("devtools gone")

        with pytest.raises(ResolveError):
            ElementResolver(vision=_FakeVision(), screen=None, dom=_Broken()).resolve("x")


# --- OCR provisioning --------------------------------------------------------

class TestOcrProvisioning:
    def test_availability_requires_the_engine_not_just_the_wrapper(self, monkeypatch):
        """The old bug: pytesseract imports fine with no tesseract.exe."""
        from seedcode.computer import ocr

        ocr.reset_cache()
        monkeypatch.setattr(ocr, "wrapper_installed", lambda: True)
        monkeypatch.setattr(ocr, "tesseract_path", lambda: None)
        assert ocr.available() is False
        ok, detail = ocr.status()
        assert ok is False
        assert "engine was not found" in detail

    def test_status_explains_a_missing_wrapper(self, monkeypatch):
        from seedcode.computer import ocr

        ocr.reset_cache()
        monkeypatch.setattr(ocr, "wrapper_installed", lambda: False)
        ok, detail = ocr.status()
        assert ok is False
        assert "pytesseract" in detail

    def test_status_reports_a_present_but_broken_engine(self, monkeypatch, tmp_path):
        from seedcode.computer import ocr

        ocr.reset_cache()
        fake = tmp_path / "tesseract.exe"
        fake.write_text("not really an executable")
        monkeypatch.setattr(ocr, "wrapper_installed", lambda: True)
        monkeypatch.setattr(ocr, "configure", lambda: str(fake))
        monkeypatch.setattr(ocr, "engine_works", lambda exe=None: False)
        ok, detail = ocr.status()
        assert ok is False
        assert "did not run" in detail

    def test_env_var_pins_the_engine(self, monkeypatch, tmp_path):
        from seedcode.computer import ocr

        ocr.reset_cache()
        pinned = tmp_path / "tesseract.exe"
        pinned.write_text("x")
        monkeypatch.setenv("SEEDCODE_TESSERACT", str(pinned))
        assert ocr.tesseract_path() == str(pinned)

    def test_bundled_locations_are_searched(self):
        """The installer's drop point must be on the search path."""
        from seedcode.computer import ocr

        paths = [str(p).lower() for p in ocr.candidate_paths()]
        assert any("tesseract" in p for p in paths)

    def test_vision_delegates_availability_to_the_ocr_module(self, monkeypatch):
        from seedcode.computer import ocr, vision

        monkeypatch.setattr(ocr, "available", lambda: True)
        assert vision.ocr_available() is True
        monkeypatch.setattr(ocr, "available", lambda: False)
        assert vision.ocr_available() is False

    def test_ocr_screenshot_explains_why_it_is_unavailable(self, monkeypatch):
        from seedcode.computer import ocr, vision

        monkeypatch.setattr(ocr, "available", lambda: False)
        monkeypatch.setattr(ocr, "status", lambda: (False, "engine missing"))
        out = vision.ocr_screenshot("x.png")
        assert "engine missing" in out
