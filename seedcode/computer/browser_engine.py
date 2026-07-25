"""Browser Engine: complete web workflows as deterministic local procedures.

This module is the answer to a specific architectural failure. Browser work
used to be assembled by the *AI*: search, screenshot, reason about the pixels,
emit a click, discover a cookie banner, emit another click, fail when OCR was
missing. Every one of those steps is mechanical, and mechanical work belongs in
the engine. The AI's entire contribution should be the sentence "play Love Me
Thoda Aur on YouTube".

So the browser engine owns the whole workflow, and it is built on one central
insight: **most browser goals do not require touching the UI at all.** A URL
expresses the goal precisely. Searching YouTube is a URL. Playing a specific
video is a URL — once you know the video id, which is an HTTP lookup, not a
click on a thumbnail. Going back is a keystroke. Under this design the
"find and click the first search result" problem, which is where OCR and
vision were being dragged in, simply stops existing.

The execution ladder for any goal, cheapest and most reliable first:

1. **URL construction** — the goal is expressible as an address. Zero UI.
2. **HTTP resolution** — the goal needs a fact from the page (a video id);
   fetch and parse it, then fall back to tier 1. Still zero UI.
3. **DOM** (:mod:`.browser_cdp`) — genuine in-page interaction, done inside
   the page so it cannot miss.
4. **Accessibility / OCR / vision** — inherited from the element resolver,
   for the rare browser-chrome interaction.

Around every action, :class:`~.browser_popups.PopupManager` clears consent
walls, translate bars, and sign-in interstitials, and after every action the
engine verifies the world actually changed. Failures come back as
:class:`BrowserWorkflowError` with a human-readable reason — never as a
half-finished sequence for the AI to untangle.

Deterministic and offline apart from the page fetch itself. No AI code.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import browser as browser_driver

# Time budgets. Generous enough for a cold page load on a slow link, short
# enough that a wedged step surfaces as a clear failure inside one agent turn.
_LOAD_TIMEOUT_S = 12.0
_HTTP_TIMEOUT_S = 8.0
_SETTLE_S = 1.2

# YouTube serves search results as server-rendered JSON embedded in the page.
# Reading the first videoId from it is how the engine plays a song without ever
# looking at a thumbnail.
_VIDEO_ID_RE = re.compile(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"')
_TITLE_RE = re.compile(r'"title"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"')

# A desktop UA: YouTube serves a different (harder to parse) payload to
# unrecognised clients.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SEARCH_ENGINES = browser_driver.SEARCH_ENGINES


class BrowserWorkflowError(Exception):
    """A browser workflow could not be completed; the message reaches the AI."""


@dataclass(slots=True)
class WorkflowResult:
    """The verified outcome of a browser workflow."""

    detail: str
    url: str | None = None
    title: str | None = None
    # Expectation dict for the dispatcher's verifier (see verifier._KINDS).
    expected: dict[str, Any] | None = None
    # Popups cleared along the way — logged, never surfaced to the AI as a
    # decision it needs to make.
    popups: tuple[str, ...] = ()

    def describe(self) -> str:
        return self.detail


class BrowserEngine:
    """Executes whole browser workflows from a single high-level goal."""

    def __init__(
        self,
        controller: Any = None,
        cdp: Any = None,
        popups: Any = None,
        driver: Any = None,
        settle_s: float | None = None,
        fetch: Any = None,
    ) -> None:
        if cdp is None:
            from . import browser_cdp as cdp  # type: ignore
        if driver is None:
            # Prefer the controller's own browser driver: it is the injectable
            # seam the rest of the engine uses, so a test (or an alternative
            # front end) that supplies a fake controller never reaches the real
            # browser or the network.
            driver = getattr(controller, "browser", None) or browser_driver
        self._controller = controller
        self._cdp = cdp
        self._driver = driver
        self._settle_s = _SETTLE_S if settle_s is None else max(0.0, float(settle_s))
        # Page fetcher for the URL-resolution tier; injectable for tests.
        self._fetch_impl = fetch
        if popups is None:
            from .browser_popups import PopupManager

            popups = PopupManager(cdp=cdp, controller=controller)
        self._popups = popups

    # --- navigation ----------------------------------------------------------
    def open_url(self, url: str, *, new_tab: bool = False) -> WorkflowResult:
        """Open a URL and confirm the browser actually went there."""
        target = _normalize(url)
        if not target:
            raise BrowserWorkflowError("A URL is required.")
        self._navigate(target, new_tab=new_tab)
        popups = self._settle()
        return self._verified(
            f"opened {target}", target, popups,
            expected={"browser_url": _fragment(target)},
        )

    def new_tab(self, url: str = "about:blank") -> WorkflowResult:
        """Open a new tab, optionally at a URL."""
        target = _normalize(url) if url and url != "about:blank" else "about:blank"
        if target == "about:blank":
            # A blank tab has no address to verify; drive it through the
            # browser's own shortcut so it lands in the focused window.
            if self._cdp_live() and self._safe(lambda: self._cdp.new_tab(target)):
                popups = self._settle()
                return WorkflowResult("opened a new tab", None, None, None, popups)
            self._hotkey(["ctrl", "t"], "open a new tab")
            popups = self._settle()
            return WorkflowResult("opened a new tab", None, None, None, popups)
        return self.open_url(target, new_tab=True)

    def close_tab(self) -> WorkflowResult:
        """Close the active tab."""
        if self._cdp_live():
            tab = self._safe(lambda: self._cdp.active_tab())
            if tab is not None and self._safe(lambda: self._cdp.close_tab(tab.target_id)):
                return WorkflowResult(f"closed tab {tab.describe()}", None, tab.title)
        self._hotkey(["ctrl", "w"], "close the active tab")
        return WorkflowResult("closed the active browser tab")

    def switch_tab(self, target: str = "") -> WorkflowResult:
        """Focus another tab, chosen by title/URL fragment (or the next one)."""
        wanted = (target or "").strip().lower()
        if self._cdp_live():
            tabs = self._safe(lambda: self._cdp.list_tabs()) or []
            if tabs:
                chosen = _pick_tab(tabs, wanted)
                if chosen is None:
                    raise BrowserWorkflowError(
                        f'No open tab matches "{target}". Open tabs: '
                        + "; ".join(t.describe() for t in tabs[:8])
                    )
                if self._safe(lambda: self._cdp.activate_tab(chosen.target_id)):
                    self._focus_browser_window()
                    return WorkflowResult(
                        f"switched to tab {chosen.describe()}", chosen.url, chosen.title,
                        expected={"browser_url": _fragment(chosen.url)} if chosen.url else None,
                    )
        # No DevTools: Ctrl+Tab cycles, which honours "the next tab" only.
        if wanted:
            raise BrowserWorkflowError(
                f'Cannot switch to a named tab ("{target}") without a DevTools '
                "connection. Use open_url to reach the page directly instead."
            )
        self._hotkey(["ctrl", "tab"], "switch browser tab")
        return WorkflowResult("switched to the next browser tab")

    def back(self) -> WorkflowResult:
        """Go back one entry in history."""
        return self._history_step("back", "alt+left", -1)

    def forward(self) -> WorkflowResult:
        """Go forward one entry in history."""
        return self._history_step("forward", "alt+right", 1)

    def refresh(self) -> WorkflowResult:
        """Reload the current page."""
        before = self._current_url()
        if not (self._cdp_live() and self._safe(lambda: self._cdp.evaluate("(location.reload(), true)"))):
            self._hotkey(["f5"], "refresh the page")
        popups = self._settle()
        url = self._current_url() or before
        return WorkflowResult(
            "refreshed the current page", url, self._current_title(), popups=popups
        )

    def _history_step(self, name: str, hotkey: str, delta: int) -> WorkflowResult:
        # Only DevTools can observe history movement. The fallback URL is
        # "the last address we asked for", which does NOT change when the user
        # goes back — so treating it as evidence would report every successful
        # keyboard-driven back as a failure.
        live = self._cdp_live()
        before = self._current_url() if live else None
        if not (live and self._safe(lambda: self._cdp.evaluate(f"(history.go({delta}), true)"))):
            self._hotkey(hotkey.split("+"), f"go {name}")
        popups = self._settle()
        if not live:
            return WorkflowResult(f"went {name}", None, None, popups=popups)
        after = self._current_url()
        if before and after and before == after:
            raise BrowserWorkflowError(
                f"Could not go {name}: the page did not change "
                f"(still {after}). There may be no {name} history."
            )
        return WorkflowResult(
            f"went {name}" + (f" to {after}" if after else ""),
            after, self._current_title(), popups=popups,
        )

    # --- search --------------------------------------------------------------
    def search(self, query: str, engine: str = "google") -> WorkflowResult:
        """Run a search — built as a URL, so no search box is ever clicked."""
        query = (query or "").strip()
        if not query:
            raise BrowserWorkflowError("A search query is required.")
        key = (engine or "google").strip().lower()
        template = SEARCH_ENGINES.get(key)
        if template is None:
            raise BrowserWorkflowError(
                f'Unknown search engine "{engine}". '
                f"Available: {', '.join(sorted(SEARCH_ENGINES))}."
            )
        url = template.format(q=urllib.parse.quote_plus(query))
        self._navigate(url)
        popups = self._settle()
        return self._verified(
            f'searched {key} for "{query}"', url, popups,
            expected={"browser_url": _fragment(url)},
        )

    def google_search(self, query: str) -> WorkflowResult:
        return self.search(query, "google")

    def youtube_search(self, query: str) -> WorkflowResult:
        return self.search(query, "youtube")

    # --- YouTube playback ----------------------------------------------------
    def youtube_play(self, query: str) -> WorkflowResult:
        """Play the best match for ``query`` on YouTube.

        The whole point of the refactor lives here. Rather than search, look at
        the results, and click a thumbnail — the sequence that needed OCR and
        produced a cascade of AI clicks — the engine resolves the video id over
        HTTP and navigates straight to the watch URL with autoplay. One
        navigation, nothing to see, nothing to click.

        If the lookup fails (offline, markup change), it degrades to opening
        the results page and clicking the first result *in the DOM*, and only
        reports success when a watch page is actually open.
        """
        query = (query or "").strip()
        if not query:
            raise BrowserWorkflowError("A search query is required.")

        video = self._resolve_youtube_video(query)
        if video is not None:
            video_id, title = video
            url = f"https://www.youtube.com/watch?v={video_id}&autoplay=1"
            self._navigate(url)
            popups = self._settle()
            self._ensure_playing()
            label = title or query
            return self._verified(
                f'playing "{label}" on YouTube', url, popups,
                expected={"browser_url": f"watch?v={video_id}"},
                title=title,
            )
        return self._play_via_results(query)

    def _resolve_youtube_video(self, query: str) -> tuple[str, str | None] | None:
        """First video id + title for a query, straight from the results HTML."""
        url = SEARCH_ENGINES["youtube"].format(q=urllib.parse.quote_plus(query))
        html = self._fetch(url)
        if not html:
            return None
        match = _VIDEO_ID_RE.search(html)
        if match is None:
            return None
        video_id = match.group(1)
        title = None
        title_match = _TITLE_RE.search(html[match.start():match.start() + 4000])
        if title_match:
            try:
                title = json.loads(f'"{title_match.group(1)}"')
            except ValueError:
                title = title_match.group(1)
        return video_id, title

    def _play_via_results(self, query: str) -> WorkflowResult:
        """Fallback: open results and click the first video inside the page."""
        self.youtube_search(query)
        if not self._cdp_live():
            raise BrowserWorkflowError(
                f'Could not resolve a video for "{query}" (YouTube did not '
                "return a parseable result and no DevTools connection is "
                "available to click one). The search results are open in the "
                "browser."
            )
        clicked = self._safe(
            lambda: self._cdp.click_selector("a#video-title, ytd-video-renderer a#thumbnail")
        )
        if not clicked:
            raise BrowserWorkflowError(
                f'Opened YouTube results for "{query}" but could not start a '
                "video: no playable result was found on the page."
            )
        popups = self._settle()
        url = self._current_url() or ""
        if "watch" not in url:
            raise BrowserWorkflowError(
                f'Clicked the first YouTube result for "{query}" but no watch '
                f"page opened (currently at {url or 'an unknown page'})."
            )
        self._ensure_playing()
        return self._verified(
            f'playing the first YouTube result for "{query}"', url, popups,
            expected={"browser_url": "watch"},
        )

    def _ensure_playing(self) -> None:
        """Nudge the player if autoplay was blocked (best-effort, never fatal).

        Chromium blocks autoplay with sound on pages the user has not
        interacted with. A DevTools ``play()`` carries a user gesture, so it
        starts reliably where a synthetic mouse click would not.
        """
        if not self._cdp_live():
            return
        self._safe(lambda: self._cdp.evaluate(
            "(() => { const v = document.querySelector('video');"
            " if (!v) return false;"
            " if (v.paused) { v.play().catch(() => {}); }"
            " return true; })()"
        ))

    # --- information ---------------------------------------------------------
    def page_info(self) -> WorkflowResult:
        """Current page title and address."""
        url = self._current_url()
        title = self._current_title()
        if not url and not title:
            return WorkflowResult("no browser page is open")
        lines = [f"Title: {title}"] if title else []
        if url:
            lines.append(f"URL: {url}")
        return WorkflowResult("\n".join(lines), url, title)

    def which_browser(self) -> str:
        return self._driver.default_browser().describe()

    def dismiss_popups(self) -> WorkflowResult:
        """Run a popup sweep on demand (the workflows do this automatically)."""
        result = self._popups.sweep()
        return WorkflowResult(result.describe(), popups=tuple(result.dismissed))

    # --- internals -----------------------------------------------------------
    def _navigate(self, url: str, *, new_tab: bool = False) -> None:
        """Drive the browser to ``url`` — DevTools when live, else the OS."""
        if not new_tab and self._cdp_live():
            if self._safe(lambda: self._cdp.navigate(url)):
                self._safe(lambda: self._cdp.wait_for_load(_LOAD_TIMEOUT_S))
                return
        try:
            self._driver.navigate(url, new_window=False)
        except Exception as exc:
            raise BrowserWorkflowError(f"Could not open {url}: {exc}")
        self._safe(lambda: self._cdp.wait_for_load(_LOAD_TIMEOUT_S))

    def _settle(self) -> tuple[str, ...]:
        """Let the page paint, then clear whatever popped up over it."""
        if self._settle_s:
            time.sleep(self._settle_s)
        result = self._popups.sweep()
        return tuple(getattr(result, "dismissed", ()) or ())

    def _verified(
        self,
        detail: str,
        url: str | None,
        popups: tuple[str, ...],
        *,
        expected: dict[str, Any] | None = None,
        title: str | None = None,
    ) -> WorkflowResult:
        """Confirm the browser really is where we asked it to go.

        When DevTools is live the live address is authoritative and a mismatch
        is a hard failure. Without it, the dispatcher's verifier still checks
        the window title through the ``expected`` dict, so success is never
        claimed on the strength of "we called navigate()".
        """
        live = self._current_url()
        if live and url:
            if not _same_page(live, url):
                raise BrowserWorkflowError(
                    f"Navigation did not land: asked for {url}, browser is at {live}."
                )
        note = f" (cleared {', '.join(popups)})" if popups else ""
        return WorkflowResult(
            detail + note, url, title or self._current_title(), expected, popups
        )

    def _cdp_live(self) -> bool:
        return bool(self._safe(lambda: self._cdp.is_available()))

    def _current_url(self) -> str | None:
        if self._cdp_live():
            url = self._safe(lambda: self._cdp.current_url())
            if url:
                return str(url)
        return self._safe(lambda: self._driver.current_url())

    def _current_title(self) -> str | None:
        if self._cdp_live():
            title = self._safe(lambda: self._cdp.current_title())
            if title:
                return str(title)
        return None

    def _focus_browser_window(self) -> None:
        """Bring the browser to the foreground (best-effort)."""
        if self._controller is None:
            return
        name = self._safe(lambda: self._driver.default_browser().name)
        if name:
            self._safe(lambda: self._controller.focus_window(name))

    def _hotkey(self, keys: list[str], what: str) -> None:
        """Send a browser keyboard shortcut, focusing the browser first."""
        if self._controller is None:
            raise BrowserWorkflowError(
                f"Cannot {what}: no desktop controller is available."
            )
        self._focus_browser_window()
        try:
            self._controller.hotkey(keys)
        except Exception as exc:
            raise BrowserWorkflowError(f"Could not {what}: {exc}")

    def _fetch(self, url: str) -> str | None:
        """GET a page as text (None on any failure — callers degrade).

        Injectable via the constructor so tests, and any offline deployment,
        can resolve results without reaching the network.
        """
        if self._fetch_impl is not None:
            return self._safe(lambda: self._fetch_impl(url))
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": _UA,
                "Accept-Language": "en-US,en;q=0.9",
                # Skip the EU consent interstitial that otherwise replaces the
                # results payload for unauthenticated fetches.
                "Cookie": "CONSENT=YES+1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, ValueError):
            return None

    @staticmethod
    def _safe(call):
        """Run a best-effort driver call; any failure becomes ``None``."""
        try:
            return call()
        except Exception:
            return None


# --- helpers -----------------------------------------------------------------

def _normalize(url: str) -> str:
    """Coerce user/AI-supplied text into a real URL."""
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith(("http://", "https://", "file://", "about:", "ftp://")):
        return url
    return "https://" + url


def _fragment(url: str) -> str:
    """A short, stable piece of a URL for the verifier to match on."""
    stripped = (url or "").split("//")[-1]
    host = stripped.split("/")[0]
    return (host or stripped)[:30]


def _same_page(live: str, wanted: str) -> bool:
    """Whether the live address satisfies the one we asked for.

    Deliberately forgiving: sites append tracking parameters, normalise
    ``www.``, and redirect ``/`` to a locale path. The check that matters is
    that we are on the intended *site and resource*, not a byte-identical URL.
    """
    live_p = urllib.parse.urlparse(live.lower())
    want_p = urllib.parse.urlparse(wanted.lower())
    live_host = live_p.netloc.removeprefix("www.")
    want_host = want_p.netloc.removeprefix("www.")
    if want_host and live_host and want_host != live_host:
        return False
    # A watch URL must still be the same video, not merely the same host.
    want_video = urllib.parse.parse_qs(want_p.query).get("v", [""])[0]
    if want_video:
        return urllib.parse.parse_qs(live_p.query).get("v", [""])[0] == want_video
    want_path = want_p.path.rstrip("/")
    if want_path and want_path not in live_p.path.rstrip("/"):
        # A search results page keeps its path but carries the query; treat a
        # matching path prefix as sufficient.
        return False
    return True


def _pick_tab(tabs: list, wanted: str):
    """Choose the tab matching a title/URL fragment (or the next one)."""
    if not tabs:
        return None
    if not wanted:
        return tabs[1] if len(tabs) > 1 else tabs[0]
    for tab in tabs:
        if wanted in (tab.title or "").lower() or wanted in (tab.url or "").lower():
            return tab
    return None
