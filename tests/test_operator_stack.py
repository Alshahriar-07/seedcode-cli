"""Tests for the operator stack: screen intelligence, semantic actions,
apps, web extraction, memory, permissions, and model-independent identity.

Every OS surface (vision walker, window driver, filesystem, winget, DevTools)
is injected as a fake — no test touches a real desktop, browser, or package
manager. The contracts pinned here are the ones the architecture doc claims:

* the AI reasons over **semantic element ids**, never coordinates;
* stale elements are re-validated, never clicked blind;
* app launching prefers focusing a running instance and verifies evidence;
* installation is gated behind a sensitive, never-remembered permission and
  refuses untrusted sources;
* web extraction is DOM-based with source traceability;
* memory is indexed, secret-free, and corruption-tolerant;
* personality survives a model switch, and permissions survive any model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from seedcode.apps.discovery import AppInfo, find_app
from seedcode.apps.installer import InstallPlan, install_app, resolve_install_plan
from seedcode.apps.launcher import launch_app
from seedcode.apps.verifier import find_app_window
from seedcode.computer import screen_state
from seedcode.computer.browser_extract import WebExtractor, reset_web_extractor
from seedcode.computer.permissions import (
    CATEGORY_INSTALL,
    CATEGORY_NETWORK,
    SENSITIVE_CATEGORIES,
    DesktopGrant,
    DesktopSession,
    SessionPermissionManager,
)
from seedcode.computer.screen_state import ScreenEngine
from seedcode.computer.semantic import SemanticActions
from seedcode.core.errors import (
    ApplicationNotFoundError,
    ElementNotFoundError,
    SecurityError,
    StaleElementError,
    WebPageNotConnectedError,
)
from seedcode.core.identity import build_system_prompt
from seedcode.core.identity_store import IdentityProfile, load_identity, save_identity
from seedcode.memory.store import MemoryStore, mask_secret
from seedcode.tools.permissions import PermissionError_, PermissionLevel


# --- fakes ---------------------------------------------------------------------------

class FakeVisionElement:
    def __init__(self, role, name, x, y, w=80, h=30, enabled=True, aid="", value=""):
        self.role, self.name, self.x, self.y = role, name, x, y
        self.width, self.height, self.enabled = w, h, enabled
        self.automation_id, self.value = aid, value
        self.control_type = role.capitalize() + "Control"


class FakeVision:
    def __init__(self, elements): self.elements = list(elements)

    def snapshot(self, window_title=None):
        return ("Fake Window", list(self.elements))


class FakeWindow:
    def __init__(self, title, pid=100, active=False, minimized=False):
        self.title, self.pid = title, pid
        self.active, self.minimized = active, minimized
        self.left, self.top, self.width, self.height = 10, 20, 800, 600


class FakeWindows:
    def __init__(self, windows): self.windows = list(windows)

    def list_windows(self): return list(self.windows)


class FakeController:
    def __init__(self):
        self.calls: list[tuple] = []

    def mouse_click(self, x, y, button="left", double=False):
        self.calls.append(("click", x, y, button, double))

    def type_text(self, text):
        self.calls.append(("type", text))

    def hotkey(self, keys):
        self.calls.append(("hotkey", tuple(keys)))


def make_engine(elements=None, windows=None) -> ScreenEngine:
    return ScreenEngine(
        vision=FakeVision(elements or []),
        windows=FakeWindows(windows or [FakeWindow("Fake Window", active=True)]),
    )


# --- screen intelligence engine ---------------------------------------------------------

class TestScreenEngine:
    def test_semantic_ids_assigned_deterministically(self):
        engine = make_engine([
            FakeVisionElement("button", "Play", 100, 200),
            FakeVisionElement("edit", "Search", 300, 50),
        ])
        snap = engine.refresh()
        assert [e.id for e in snap.elements] == ["element_001", "element_002"]
        assert snap.elements[0].role == "button"
        assert snap.elements[0].center == (100, 200)

    def test_unchanged_desktop_reuses_cache_and_generation(self):
        engine = make_engine([FakeVisionElement("button", "Play", 1, 2)])
        first = engine.refresh()
        second = engine.refresh()
        assert second.generation == first.generation
        assert second is first  # same snapshot object: no rescan cost

    def test_changed_elements_bump_generation(self):
        engine = make_engine([FakeVisionElement("button", "Play", 1, 2)])
        engine.refresh()
        engine._vision.elements.append(FakeVisionElement("edit", "Box", 5, 5))
        snap = engine.refresh()
        assert snap.generation == 2
        assert len(snap.elements) == 2

    def test_windows_driver_failure_degrades_to_empty(self):
        class Exploding:
            def list_windows(self): raise RuntimeError("no desktop")

        engine = ScreenEngine(vision=FakeVision([]), windows=Exploding())
        snap = engine.refresh()
        assert snap.windows == [] and snap.active_window is None

    def test_find_element_matches_name_and_prefers_enabled(self):
        engine = make_engine([
            FakeVisionElement("button", "Play", 10, 10, enabled=False),
            FakeVisionElement("button", "Play", 20, 20, enabled=True),
        ])
        el = engine.find_element("Play button", fresh=True)
        assert el.enabled and el.center == (20, 20)

    def test_find_element_not_found_is_structured(self):
        engine = make_engine([FakeVisionElement("button", "Play", 1, 2)])
        with pytest.raises(ElementNotFoundError):
            engine.find_element("Nonexistent", fresh=True)

    def test_get_element_validates_freshness_when_identity_gone(self):
        engine = make_engine([FakeVisionElement("button", "Play", 1, 2)])
        engine.refresh()
        engine._vision.elements.clear()  # the UI changed under us
        with pytest.raises(StaleElementError):
            engine.get_element("element_001")

    def test_get_element_returns_moved_element_at_new_position(self):
        """The 'UI moved' case: identity triple still resolves → fresh coords."""
        engine = make_engine([FakeVisionElement("button", "Play", 1, 2)])
        engine.refresh()
        engine._vision.elements[0].x, engine._vision.elements[0].y = 500, 400
        el = engine.get_element("element_001")
        assert el.id == "element_001"  # stable reference
        assert el.center == (500, 400)  # live position

    def test_state_json_shape(self):
        engine = make_engine(
            [FakeVisionElement("button", "Play", 100, 200, 80, 40)],
            windows=[FakeWindow("YouTube - Google Chrome", pid=1234, active=True)],
        )
        state = engine.state_json()
        assert state["active_window"]["app"] == "Google Chrome"
        assert state["active_window"]["pid"] == 1234
        win = state["windows"][0]
        assert win["id"] == "window_001" and win["visible"] and win["focused"]
        el = state["elements"][0]
        assert el["id"] == "element_001" and el["center"] == [100, 200]
        assert el["bounds"] == [60, 180, 80, 40]

    def test_wait_for_element_polls_until_present(self):
        engine = make_engine([])
        engine._vision.elements.append(FakeVisionElement("button", "Late", 1, 1))
        el = engine.wait_for_element("Late", timeout_s=2.0)
        assert el.name == "Late"

    def test_wait_for_element_times_out_bounded(self):
        engine = make_engine([])
        with pytest.raises(ElementNotFoundError):
            engine.wait_for_element("Ghost", timeout_s=0.1)


# --- semantic actions ------------------------------------------------------------------

class TestSemanticActions:
    def test_click_uses_element_coordinates_not_model_supplied_ones(self):
        engine = make_engine([FakeVisionElement("button", "Play", 111, 222)])
        controller = FakeController()
        actions = SemanticActions(engine=engine, controller=controller)
        detail = actions.click("element_001")
        assert controller.calls == [("click", 111, 222, "left", False)]
        assert "Play" in detail

    def test_type_into_focuses_first_then_types(self):
        engine = make_engine([FakeVisionElement("edit", "Search", 5, 6)])
        controller = FakeController()
        actions = SemanticActions(engine=engine, controller=controller)
        actions.type_into("element_001", "tere naina")
        assert ("click", 5, 6, "left", False) in controller.calls
        assert ("type", "tere naina") in controller.calls

    def test_type_into_secret_is_masked_in_result(self):
        engine = make_engine([FakeVisionElement("edit", "Password", 5, 6)])
        actions = SemanticActions(engine=engine, controller=FakeController())
        detail = actions.type_into("element_001", "hunter2", secret=True)
        assert "hunter2" not in detail and "••" in detail

    def test_click_after_ui_moved_targets_new_position(self):
        """Never blindly click an old coordinate after the UI changed."""
        engine = make_engine([FakeVisionElement("button", "OK", 10, 10)])
        engine.refresh()
        engine._vision.elements[0].x, engine._vision.elements[0].y = 700, 300
        controller = FakeController()
        SemanticActions(engine=engine, controller=controller).click("element_001")
        assert controller.calls[0][:3] == ("click", 700, 300)

    def test_click_on_vanished_element_raises_stale(self):
        engine = make_engine([FakeVisionElement("button", "OK", 10, 10)])
        engine.refresh()
        engine._vision.elements.clear()
        with pytest.raises(StaleElementError):
            SemanticActions(engine=engine, controller=FakeController()).click("element_001")

    def test_check_respects_current_state(self):
        engine = make_engine([FakeVisionElement("checkbox", "Mute", 1, 2)])
        controller = FakeController()
        actions = SemanticActions(engine=engine, controller=controller)
        detail = actions.check("element_001", checked=False)
        assert "already" in detail  # element not yet checked → no, wait:
        # element.checked defaults False, asking to uncheck → already unchecked.
        assert controller.calls == []


# --- application discovery / launcher ----------------------------------------------------

class TestAppDiscovery:
    def _shortcut(self, name):
        from pathlib import Path
        return Path(f"C:/sm/{name}.lnk")

    def test_find_app_exact_start_menu_match(self):
        app = find_app("Spotify", start_menu=[self._shortcut("Spotify")])
        assert app.source == "start_menu" and app.name == "Spotify"
        assert app.target.endswith("Spotify.lnk")

    def test_find_app_partial_match(self):
        app = find_app("visual studio", start_menu=[self._shortcut("Visual Studio Code")])
        assert app.name == "Visual Studio Code"

    def test_find_app_missing_raises_structured_error(self):
        with pytest.raises(ApplicationNotFoundError):
            find_app("Definitely Not Installed", start_menu=[])


@dataclass
class _Win:
    title: str
    pid: int = 0


class _WinDriver:
    def __init__(self, windows): self.windows = list(windows); self.focused = []

    def list_windows(self): return list(self.windows)

    def focus_window(self, title): self.focused.append(title)


class TestAppLauncher:
    def _app(self, name="Spotify"):
        return AppInfo(name=name, source="start_menu", target="C:/sm/Spotify.lnk")

    def test_running_instance_is_focused_not_duplicated(self):
        driver = _WinDriver([_Win("Spotify - Main")])
        result = launch_app(
            "Spotify", find=lambda name: self._app(),
            windows_driver=driver, startfile=lambda t: pytest.fail("must not launch"),
            popen=lambda a: pytest.fail("must not launch"), wait_s=0.05,
        )
        assert result.success and result.focused_existing and result.window_detected
        assert driver.focused == ["Spotify - Main"]

    def test_launch_via_shortcut_then_window_verified(self):
        driver = _WinDriver([])
        started = []
        driver.list_windows = lambda: [_Win("Spotify")] if started else []
        def fake_startfile(target):
            started.append(target)
        result = launch_app(
            "Spotify", find=lambda name: self._app(),
            windows_driver=driver, startfile=fake_startfile,
            popen=lambda a: pytest.fail("lnk must use startfile"), wait_s=2.0,
        )
        assert started == ["C:/sm/Spotify.lnk"]
        assert result.success and result.window_detected
        assert "window" in result.detail.lower()

    def test_launch_without_window_evidence_is_reported_unverified(self):
        driver = _WinDriver([])
        result = launch_app(
            "Spotify", find=lambda name: self._app(),
            windows_driver=driver, startfile=lambda t: None, popen=lambda a: None,
            wait_s=0.05,
        )
        assert not result.success and not result.window_detected

    def test_unknown_target_without_launchable_file(self):
        app = AppInfo(name="Ghost", source="uninstall", install_location="")
        with pytest.raises(Exception):
            launch_app("Ghost", find=lambda name: app, windows_driver=_WinDriver([]),
                       startfile=lambda t: None, popen=lambda a: None, wait_s=0.05)


class TestAppVerifier:
    def test_window_matched_by_process_id_first(self):
        app = self._app()
        driver = _WinDriver([_Win("Something Else", pid=7), _Win("Spotify", pid=app.pid or 5)])
        app.pid = 5
        assert find_app_window(app, driver) == "Spotify"

    def _app(self):
        return AppInfo(name="Spotify", source="start_menu", target="C:/sm/Spotify.lnk")

    def test_no_window_returns_none(self):
        assert find_app_window(self._app(), _WinDriver([_Win("Other")])) is None


# --- installation (permission-gated) --------------------------------------------------------

class TestInstaller:
    def test_no_winget_means_no_trusted_source(self, monkeypatch):
        import seedcode.apps.installer as inst
        monkeypatch.setattr(inst.shutil, "which", lambda name: None)
        with pytest.raises(SecurityError) as exc:
            resolve_install_plan("VLC")
        assert "will not download" in str(exc.value)

    def test_winget_plan_is_trusted(self, monkeypatch):
        import seedcode.apps.installer as inst
        monkeypatch.setattr(inst.shutil, "which", lambda name: "C:/winget.exe" if name == "winget" else None)
        def fake_run(args):
            @dataclass
            class P: returncode: int = 0; stdout: str = ""; stderr: str = ""
            return P(0, "VLC media player  VLC.VLC  3.0.20", "")
        plan = resolve_install_plan("VLC", run=fake_run)
        assert plan.mechanism == "winget" and plan.package_id == "VLC"

    def test_install_requires_explicit_confirmation(self, monkeypatch):
        import seedcode.apps.installer as inst
        monkeypatch.setattr(inst.shutil, "which", lambda name: "C:/winget.exe" if name == "winget" else None)
        ran = []
        def fake_run(args, **kw):
            ran.append(args)
            @dataclass
            class P: returncode: int = 0; stdout: str = ""; stderr: str = ""
            # Search must surface the package; install must report success.
            return P(0, "VLC media player  VLC.VLC  3.0.20" if "search" in args else "installed", "")
        def fake_find(name):
            return AppInfo(name=name, source="start_menu", target="x")
        result = install_app(
            "VLC", confirm=lambda plan_text: False,  # the user says NO
            run=fake_run, find=fake_find,
        )
        assert not result.success and "declined" in result.detail
        # Only the read-only winget *search* ran; no install command executed.
        assert all(args[1] == "search" for args in ran), ran

    def test_install_verifies_discoverability_after_success(self, monkeypatch):
        import seedcode.apps.installer as inst
        monkeypatch.setattr(inst.shutil, "which", lambda name: "C:/winget.exe" if name == "winget" else None)
        @dataclass
        class P: returncode: int = 0; stdout: str = ""; stderr: str = ""
        def fake_find(name):
            raise ApplicationNotFoundError("not discoverable yet")
        def fake_run(args, **kw):
            return P(0, "VLC media player  VLC.VLC" if "search" in args else "installed", "")
        result = install_app(
            "VLC", confirm=lambda plan_text: True, run=fake_run,
            find=fake_find,
        )
        assert not result.success and "not discoverable" in result.detail


# --- web extraction ---------------------------------------------------------------------------

class FakeCDP:
    """Dispatches the extractor's JS payloads to canned page data."""

    def __init__(self, available=True, data=None):
        self.available = available
        self.data = data or {}
        self.evaluated: list[str] = []

    def is_available(self): return self.available

    def evaluate(self, js):
        self.evaluated.append(js)
        if "location.href" in js: return self.data.get("url", "https://example.com/page")
        if "document.title" in js: return self.data.get("title", "Example Page")
        if "readyState" in js: return True
        if "querySelectorAll('table')" in js:
            return self.data.get("tables", [[["Name", "Price"], ["Widget", "$9"]]])
        if "a[href]" in js: return self.data.get("links", [{"text": "Home", "href": "https://example.com/"}])
        if "h1,h2,h3" in js: return self.data.get("headings", [{"level": 1, "text": "H"}])
        if "'ul,ol'" in js or 'ul,ol' in js: return self.data.get("lists", [["a", "b"]])
        if 'meta[name="description"]' in js:
            return self.data.get("meta", {"description": "d", "og_title": "t"})
        if "application/ld+json" in js: return self.data.get("ld", [])
        if "TreeWalker" in js: return self.data.get("find", [{"text": "price is $9", "tag": "p"}])
        if "innerText" in js: return self.data.get("text", "Hello world")
        if "querySelector" in js: return True  # wait_for_element
        return None


class TestWebExtraction:
    def setup_method(self):
        reset_web_extractor()

    def test_page_text_carries_source_traceability(self):
        ex = WebExtractor(cdp=FakeCDP())
        out = ex.page_text().to_dict()
        assert out["kind"] == "text" and out["content"] == "Hello world"
        assert out["source_url"] == "https://example.com/page"
        assert out["page_title"] == "Example Page"
        assert out["extraction_method"] == "dom"
        assert out["retrieved_at"]

    def test_tables_return_rows(self):
        ex = WebExtractor(cdp=FakeCDP())
        out = ex.tables().to_dict()
        assert out["content"] == [[["Name", "Price"], ["Widget", "$9"]]]

    def test_links_filter(self):
        ex = WebExtractor(cdp=FakeCDP())
        ex._eval = lambda js: [{"text": "Home", "href": "https://e.com/"},
                               {"text": "Best price", "href": "https://e.com/p"}]
        out = ex.links(filter_text="price").to_dict()
        assert len(out["content"]) == 1 and out["content"][0]["text"] == "Best price"

    def test_not_connected_raises_structured_error(self):
        ex = WebExtractor(cdp=FakeCDP(available=False))
        with pytest.raises(WebPageNotConnectedError):
            ex.page_text()

    def test_find_on_page_requires_query(self):
        ex = WebExtractor(cdp=FakeCDP())
        from seedcode.core.errors import ExtractionError
        with pytest.raises(ExtractionError):
            ex.find_on_page("   ")

    def test_wait_for_element_returns_quickly_when_present(self):
        ex = WebExtractor(cdp=FakeCDP())
        assert ex.wait_for_element("#price", timeout_s=0.5) is True


# --- persistent memory --------------------------------------------------------------------------

class TestMemoryStore:
    def test_put_get_roundtrip(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        assert store.put("web", "page:example", "Example product page",
                         {"price": "$9"}, tags=["shopping"])
        record = store.get("web", "page:example")
        assert record.summary == "Example product page"
        assert record.data == {"price": "$9"}

    def test_index_query_matches_text_and_tags(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.put("web", "p1", "Wireless mouse product page", tags=["shopping"])
        store.put("web", "p2", "News article about mice", tags=["news"])
        hits = store.query("web", text="mouse")
        assert [h["id"] for h in hits] == ["p1"]  # tag match ranks p1 first via tags
        both = store.query("web", text="", tags=["shopping"])
        assert [h["id"] for h in both] == ["p1"]

    def test_query_reads_index_not_records(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.put("user", "pref", "prefers dark theme")
        record_file = next((tmp_path / "user").glob("pref*.json"))
        record_file.unlink()
        # The index still lists it, query works without the record file:
        assert store.query("user", text="dark")[0]["id"] == "pref"
        assert store.get("user", "pref") is None  # record load degrades safely

    def test_secrets_rejected_at_write_time(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        with pytest.raises(SecurityError):
            store.put("user", "c", "creds", {"password": "hunter2"})
        with pytest.raises(SecurityError):
            store.put("user", "c", "creds", {"nested": {"api_key": "x"}})
        with pytest.raises(SecurityError):
            store.put("user", "c", "creds", {"token": "x"})
        assert store.get("user", "c") is None  # nothing was written

    def test_corrupt_index_degrades_to_empty(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.put("web", "p1", "s")
        (tmp_path / "web" / "index.json").write_text("{not json", encoding="utf-8")
        assert store.query("web", text="s") == []  # contained, no crash

    def test_invalid_namespace_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            MemoryStore(root=tmp_path).put("not-a-ns", "x", "s")

    def test_delete_removes_record_and_index_entry(self, tmp_path):
        store = MemoryStore(root=tmp_path)
        store.put("web", "p1", "s")
        assert store.delete("web", "p1")
        assert store.get("web", "p1") is None
        assert store.query("web", text="s") == []

    def test_mask_secret(self):
        assert mask_secret("hunter2") == "hu*****"
        assert mask_secret("ab") == "**"


# --- permission expansion -------------------------------------------------------------------------

class TestPermissionExpansion:
    def _session(self, choices: list[DesktopGrant]):
        asked: list[tuple[str, str]] = []

        def confirm(category, description):
            asked.append((category, description))
            return choices.pop(0)

        return DesktopSession(enabled=True, confirm=confirm), asked

    def test_install_is_sensitive_and_never_remembered(self):
        session, asked = self._session([DesktopGrant.ALWAYS, DesktopGrant.ALWAYS])
        session.check(CATEGORY_INSTALL, "Install VLC via winget")
        session.check(CATEGORY_INSTALL, "Install GIMP via winget")
        assert len(asked) == 2  # re-asked every time: "Always" is impossible
        assert CATEGORY_INSTALL not in session.grants

    def test_install_can_be_denied(self):
        session, _ = self._session([DesktopGrant.DENY])
        with pytest.raises(PermissionError_):
            session.check(CATEGORY_INSTALL, "Install VLC")
        assert session.grants.get(CATEGORY_INSTALL) is None

    def test_network_is_routinely_grantable(self):
        session, asked = self._session([DesktopGrant.ALWAYS])
        session.check(CATEGORY_NETWORK, "download report.csv")
        session.check(CATEGORY_NETWORK, "download data.json")  # remembered
        assert len(asked) == 1
        assert session.grants[CATEGORY_NETWORK] == DesktopGrant.ALWAYS

    def test_session_grants_cannot_cover_sensitive_categories(self):
        mgr = SessionPermissionManager()
        mgr.request([CATEGORY_INSTALL, CATEGORY_NETWORK], allow=True)
        assert mgr.is_granted(CATEGORY_NETWORK)
        assert not mgr.is_granted(CATEGORY_INSTALL)  # never blanket-granted

    def test_disabled_session_blocks_everything(self):
        session = DesktopSession(enabled=False, confirm=lambda c, d: DesktopGrant.ALWAYS)
        with pytest.raises(PermissionError_):
            session.check(CATEGORY_NETWORK, "download")

    def test_sensitive_registry_complete(self):
        assert {"registry_write", "type_secret", "system", "delete",
                "purchase", "install"}.issubset(SENSITIVE_CATEGORIES)


# --- model-independent identity ----------------------------------------------------------------

class TestModelIndependence:
    def _identity_file(self, monkeypatch, tmp_path):
        import seedcode.core.identity_store as store_mod
        path = tmp_path / "identity.json"
        monkeypatch.setattr(store_mod, "identity_path", lambda: path)
        return path

    def test_owner_overrides_render_into_prompt(self, monkeypatch, tmp_path):
        path = self._identity_file(monkeypatch, tmp_path)
        save_identity(IdentityProfile(
            tone="terse and precise",
            behavior_rules=["Never claim unverified success."],
        ))
        prompt = build_system_prompt("ProviderA", "model-a")
        assert "terse and precise" in prompt
        assert "Never claim unverified success." in prompt

    def test_corrupt_identity_file_degrades_to_baseline(self, monkeypatch, tmp_path):
        path = self._identity_file(monkeypatch, tmp_path)
        path.write_text("{broken json", encoding="utf-8")
        assert load_identity().is_empty()
        prompt = build_system_prompt("ProviderA", "model-a")
        assert "You are Seed Code" in prompt  # baseline identity intact

    def test_model_switch_changes_only_the_reasoning_line(self, monkeypatch, tmp_path):
        self._identity_file(monkeypatch, tmp_path)
        a = build_system_prompt("ProviderA", "model-a")
        b = build_system_prompt("ProviderB", "model-b")
        split = "\n\nYour current reasoning engine:"
        head_a, rest_a = a.split(split, 1)
        head_b, rest_b = b.split(split, 1)
        assert head_a == head_b          # personality block identical
        assert rest_a != rest_b          # only the engine line changed
        assert "model-a" in rest_a and "model-b" in rest_b

    def test_identity_profile_render_shape(self):
        profile = IdentityProfile(identity="Custom self.", tone="calm",
                                  behavior_rules=["r1"], interaction_style=["s1"])
        text = profile.render()
        assert text.startswith("Custom self.")
        assert "Tone: calm" in text and "- r1" in text and "- s1" in text
        assert IdentityProfile().render() == ""


# --- error taxonomy + limits sanity ----------------------------------------------------------------

class TestErrorsAndLimits:
    def test_structured_codes_and_recoverability(self):
        assert ElementNotFoundError().code == "ELEMENT_NOT_FOUND"
        assert ElementNotFoundError().recoverable
        assert SecurityError().code == "SECURITY_DENIED"
        assert not SecurityError().recoverable
        err = StaleElementError("element_042")
        assert err.to_dict()["details"]["element_id"] == "element_042"

    def test_limits_are_bounded(self):
        from seedcode.core.limits import (
            MAX_ACTION_RETRIES, MAX_RECOVERY_ATTEMPTS, MAX_PLAN_STEPS, clamp_wait,
        )
        assert 0 < MAX_ACTION_RETRIES <= 5
        assert 0 < MAX_RECOVERY_ATTEMPTS <= 5
        assert 0 < MAX_PLAN_STEPS <= 100
        assert clamp_wait(-5, 10) == 0
        assert clamp_wait(99, 10) == 10


# --- skill-layer wiring (AI-facing contract) ------------------------------------------------------

@dataclass
class _FakePerms:
    desktop: Any = None

    def require(self, level, what):
        assert isinstance(level, PermissionLevel)


@dataclass
class _FakeState:
    actions: list = field(default_factory=list)

    def record_action(self, detail): self.actions.append(detail)


def _ctx(controller=None, desktop_session=None):
    from seedcode.computer.skills import SkillContext
    return SkillContext(
        controller=controller or FakeController(),
        resolver=None,
        state=_FakeState(),
        permissions=_FakePerms(desktop=desktop_session),
    )


def _inject_engine(engine):
    screen_state._ENGINE = engine


class TestOperatorSkillWiring:
    def setup_method(self):
        screen_state._ENGINE = None

    def teardown_method(self):
        screen_state._ENGINE = None

    def _registry_skill(self, name):
        import seedcode.computer.catalog  # noqa: F401 — populates REGISTRY
        from seedcode.computer.skills import REGISTRY
        return REGISTRY.get(name)

    def test_find_ui_element_returns_id_never_coordinates(self):
        _inject_engine(make_engine([FakeVisionElement("button", "Play", 100, 200)]))
        outcome = self._registry_skill("find_ui_element").run(
            _ctx(), {"query": "Play button"}
        )
        assert '"id": "element_001"' in outcome.detail
        assert "100" not in outcome.detail and "200" not in outcome.detail

    def test_click_element_routes_through_semantic_layer(self, monkeypatch):
        _inject_engine(make_engine([FakeVisionElement("button", "Play", 100, 200)]))
        import seedcode.computer.controller as controller_mod
        fake = FakeController()
        monkeypatch.setattr(controller_mod, "ComputerController", lambda: fake)
        outcome = self._registry_skill("click_element").run(
            _ctx(), {"element_id": "element_001"}
        )
        assert fake.calls == [("click", 100, 200, "left", False)]
        assert "Play" in outcome.detail

    def test_find_ui_element_missing_query_is_a_skill_error(self):
        _inject_engine(make_engine([]))
        from seedcode.computer.skills import SkillError
        with pytest.raises(SkillError):
            self._registry_skill("find_ui_element").run(_ctx(), {})

    def test_screen_state_skill_reports_structured_state(self):
        _inject_engine(make_engine([FakeVisionElement("button", "Play", 1, 2)]))
        outcome = self._registry_skill("screen_state").run(_ctx(), {})
        assert "element_001" in outcome.detail

    def test_app_open_missing_app_suggests_install_flow(self, monkeypatch):
        def boom(name):
            raise ApplicationNotFoundError('"Nope" is not installed')
        import seedcode.apps.launcher as launcher_mod
        monkeypatch.setattr(launcher_mod, "launch_app", boom)
        from seedcode.computer.skills import SkillError
        with pytest.raises(SkillError) as exc:
            self._registry_skill("app_open").run(_ctx(), {"target": "Nope"})
        assert "install" in str(exc.value).lower()

    def test_memory_save_and_search_via_skills(self, tmp_path):
        from seedcode.memory import store as store_mod
        store_mod._STORE = MemoryStore(root=tmp_path)
        try:
            self._registry_skill("memory_save").run(_ctx(), {
                "namespace": "web", "id": "page:shop", "summary": "Headset price page",
                "data": json.dumps({"price": "$55"}), "tags": "shopping,price",
            })
            outcome = self._registry_skill("memory_search").run(
                _ctx(), {"query": "headset"}
            )
            assert "page:shop" in outcome.detail
            # Secret-bearing writes are refused at the skill boundary too:
            from seedcode.computer.skills import SkillError
            with pytest.raises(SkillError):
                self._registry_skill("memory_save").run(_ctx(), {
                    "namespace": "user", "id": "c", "summary": "creds",
                    "data": json.dumps({"api_key": "k"}),
                })
        finally:
            store_mod._STORE = None

    def test_memory_get_missing_record(self, tmp_path):
        from seedcode.memory import store as store_mod
        store_mod._STORE = MemoryStore(root=tmp_path)
        try:
            from seedcode.computer.skills import SkillError
            with pytest.raises(SkillError):
                self._registry_skill("memory_get").run(
                    _ctx(), {"namespace": "web", "id": "nope"}
                )
        finally:
            store_mod._STORE = None

    def test_web_skill_surfaces_structured_error_as_text(self):
        from seedcode.computer import browser_extract as be
        be._EXTRACTOR = WebExtractor(cdp=FakeCDP(available=False))
        try:
            from seedcode.computer.skills import SkillError
            with pytest.raises(SkillError):
                self._registry_skill("web_page_info").run(_ctx(), {})
        finally:
            be._EXTRACTOR = None

    def test_operator_skill_permission_levels(self):
        registry_level = {
            "screen_state": PermissionLevel.DESKTOP,
            "find_ui_element": PermissionLevel.DESKTOP,
            "click_element": PermissionLevel.DESKTOP,
            "app_open": PermissionLevel.DESKTOP,
            "install_app": PermissionLevel.DESKTOP,
            "web_extract_text": PermissionLevel.DESKTOP,
            "memory_save": PermissionLevel.WORKSPACE,
        }
        for name, level in registry_level.items():
            skill = self._registry_skill(name)
            assert skill is not None, name
            assert skill.level == level, name
