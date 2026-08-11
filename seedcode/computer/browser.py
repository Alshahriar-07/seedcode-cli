"""Default-browser URL handler — the low-level navigation driver.

Seed Code drives the **user's own default browser** — the one already signed
in, themed, and trusted — rather than spinning up a throwaway Selenium
instance. This module is the thin part of that: handing a URL to the OS
(``webbrowser`` / ``os.startfile``), which honours whatever Chromium- or
Gecko-based browser the user set as default (Chrome, Edge, Firefox, Brave,
Opera, Vivaldi, …). No WebDriver, no driver downloads.

**Workflows do not live here.** Anything a user would recognise as a task —
"play this song", "search YouTube", "switch tabs" — belongs to
:mod:`.browser_engine`, which owns the full procedure including popup
dismissal, result selection, and verification, and is exposed to the AI as
skills in :mod:`.browser_skills`. This module is one of the mechanisms that
engine uses.

Selenium remains available as an *optional* fallback (see
:mod:`.browser_selenium`) for the rare task needing a scripted throwaway
profile; it is never the primary mechanism and is not on the AI's surface.

State (the last URL we opened, the browser family) is tracked so the verifier
and StateManager can confirm outcomes. This module is offline and holds no AI
code.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from dataclasses import dataclass
from urllib.parse import quote_plus

# The last URL we asked the OS to open — the best-effort "current URL" the
# verifier checks, since reading the live address bar requires UIA and may miss.
_last_url: str | None = None


class BrowserError(Exception):
    """A default-browser action failed; the message is fed back to the model."""


@dataclass(slots=True)
class BrowserIdentity:
    """The resolved default browser."""

    name: str        # human name, e.g. "Google Chrome"
    family: str      # normalized family: chrome|edge|firefox|brave|opera|other
    prog_id: str     # the raw Windows ProgId (empty off-Windows)

    def describe(self) -> str:
        return self.name


# Windows ProgId → (display name, family). Matched by prefix so versioned
# Firefox ProgIds (``FirefoxURL-<hash>``) and channel variants still resolve.
_PROGID_MAP: tuple[tuple[str, str, str], ...] = (
    ("ChromeHTML", "Google Chrome", "chrome"),
    ("MSEdgeHTM", "Microsoft Edge", "edge"),
    ("MSEdgeMHT", "Microsoft Edge", "edge"),
    ("FirefoxURL", "Mozilla Firefox", "firefox"),
    ("BraveHTML", "Brave", "brave"),
    ("BraveFile", "Brave", "brave"),
    ("OperaStable", "Opera", "opera"),
    ("Opera", "Opera", "opera"),
    ("VivaldiHTM", "Vivaldi", "other"),
    ("IE.HTTP", "Internet Explorer", "other"),
    ("AppXq", "Microsoft Edge", "edge"),  # Edge legacy AppX ProgIds
)

SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
}


def is_available() -> tuple[bool, str]:
    """Whether default-browser control can run here.

    A default browser effectively always exists, so this is about the platform
    having a usable URL handler. Always true in practice; kept as a tuple to
    mirror the other drivers' availability contract.
    """
    return True, ""


def default_browser() -> BrowserIdentity:
    """Resolve the user's current default browser."""
    if sys.platform == "win32":
        prog_id = _win_default_progid()
        if prog_id:
            for prefix, name, family in _PROGID_MAP:
                if prog_id.lower().startswith(prefix.lower()):
                    return BrowserIdentity(name=name, family=family, prog_id=prog_id)
            return BrowserIdentity(name=prog_id, family="other", prog_id=prog_id)
    # Non-Windows or unknown: report what the stdlib will actually use.
    try:
        controller = webbrowser.get()
        name = getattr(controller, "name", "") or type(controller).__name__
    except Exception:
        name = "system default"
    return BrowserIdentity(name=name or "system default", family="other", prog_id="")


def _win_default_progid() -> str:
    """Read the HTTPS URL-association ProgId from the registry (empty on error)."""
    try:
        import winreg

        key_path = (
            r"Software\Microsoft\Windows\Shell\Associations"
            r"\UrlAssociations\https\UserChoice"
        )
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            prog_id, _type = winreg.QueryValueEx(key, "ProgId")
            return str(prog_id or "")
    except (OSError, ValueError, ImportError):
        return ""


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "about:blank"
    if not url.startswith(("http://", "https://", "file://", "about:", "ftp://")):
        url = "https://" + url
    return url


def navigate(url: str, *, new_window: bool = False) -> str:
    """Open ``url`` in the user's default browser (a new tab by default)."""
    global _last_url
    target = _normalize_url(url)
    browser = default_browser()
    opened = _open(target, new_window=new_window)
    if not opened:
        raise BrowserError(
            f"Could not hand '{target}' to the default browser ({browser.name})."
        )
    _last_url = target
    return f"Opened {target} in {browser.name} (default browser)."


def _open(target: str, *, new_window: bool) -> bool:
    """Hand a URL to the OS default handler. Returns whether it was accepted."""
    # webbrowser.open honours the OS default and returns success; new=2 asks
    # for a new tab, new=1 a new window.
    try:
        if webbrowser.open(target, new=(1 if new_window else 2)):
            return True
    except Exception:
        pass
    # Fallback: ShellExecute the URL directly (Windows) — same default handler.
    if sys.platform == "win32":
        try:
            os.startfile(target)  # noqa: S606 — deliberate shell-open of a URL
            return True
        except OSError:
            return False
    return False


def open_new_tab(url: str) -> str:
    """Open a URL in a new tab of the default browser."""
    return navigate(url, new_window=False)


def search(query: str, engine: str = "google") -> str:
    """Run a web search in the default browser using ``engine``."""
    query = (query or "").strip()
    if not query:
        raise BrowserError("A search query is required.")
    template = SEARCH_ENGINES.get(engine.lower().strip(), SEARCH_ENGINES["google"])
    return navigate(template.format(q=quote_plus(query)))


def get_page_info() -> str:
    """Best-effort current page: the active browser window title + last URL.

    Reading the live URL from the address bar needs UIA and is not always
    exposed; the last URL we opened is a reliable floor the verifier can use.
    """
    title = _active_browser_title()
    lines = []
    if title:
        lines.append(f"Title: {title}")
    if _last_url:
        lines.append(f"URL: {_last_url}")
    if not lines:
        return "No browser page information available."
    return "\n".join(lines)


def _active_browser_title() -> str:
    """Title of a visible browser window, if one is open."""
    try:
        from . import windows as windows_driver

        browser = default_browser()
        # Chromium/Gecko windows end with the browser's name, e.g.
        # "YouTube — Google Chrome". Match on family/name tokens.
        tokens = {browser.name.lower(), browser.family.lower(),
                  "chrome", "edge", "firefox", "brave", "opera", "mozilla"}
        for w in windows_driver.list_windows():
            low = (w.title or "").lower()
            if any(tok and tok in low for tok in tokens):
                return w.title
    except Exception:
        pass
    return ""


def current_url() -> str | None:
    """The last URL Seed Code opened in the default browser (state tracking)."""
    return _last_url


def get_current_browser() -> str:
    """The default browser's display name."""
    return default_browser().name


def close_browser() -> str:
    """Close a visible browser window (best-effort, graceful)."""
    global _last_url
    title = _active_browser_title()
    if not title:
        _last_url = None
        return "No browser window found to close."
    try:
        from . import windows as windows_driver

        windows_driver.close_window(title)
    except Exception as exc:
        raise BrowserError(f"Could not close the browser window: {exc}")
    _last_url = None
    return f"Closed browser window '{title}'."


# --- optional Selenium fallback (DOM-level control) --------------------------
# These exist for the rare task that needs a scripted, throwaway browser
# profile. They are NOT the primary path and are not reachable from the AI:
# web automation goes through the Browser Engine's skills (youtube_play,
# open_url, …), which drive the user's real, signed-in browser.

def _selenium():
    from . import browser_selenium

    ok, reason = browser_selenium.is_available()
    if not ok:
        raise BrowserError(
            f"DOM-level browser control needs the optional Selenium extra ({reason}). "
            "For essentially every task, prefer the browser skills instead "
            "(youtube_play, google_search, open_url, …) — they drive the user's "
            "own browser, handle popups, and verify the result, with no extra "
            "install. ui_click is not available on browser windows."
        )
    return browser_selenium


def click_element(selector: str, selector_type: str = "css") -> str:
    return _selenium().click_element(selector, selector_type)


def type_in_element(selector: str, text: str, selector_type: str = "css") -> str:
    return _selenium().type_in_element(selector, text, selector_type)


def find_elements(selector: str, selector_type: str = "css") -> str:
    return _selenium().find_elements(selector, selector_type)


def execute_script(script: str) -> str:
    return _selenium().execute_script(script)
