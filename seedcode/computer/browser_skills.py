"""Browser skills: the complete AI-facing surface for web automation.

Each skill here takes a *goal* ("play Love Me Thoda Aur on YouTube") and hands
it to the deterministic :class:`~.browser_engine.BrowserEngine`, which performs
every step — navigation, popup dismissal, result selection, playback, and
verification — without returning to the AI in between. The AI issues exactly
one call per goal and reads back one verified outcome.

This is the boundary the refactor exists to enforce. There is deliberately no
skill that clicks "a thing on a web page": if a workflow is worth automating it
gets a named skill whose procedure lives here in code, where it is testable,
repeatable, and identical on every run. ``ui_*`` actions are refused outright
on browser windows (see :mod:`.dispatcher`) so the old click-by-click pattern
cannot come back.

Importing this module registers the skills; :mod:`.catalog` imports it.
"""

from __future__ import annotations

from typing import Any

from ..tools.permissions import PermissionLevel
from .browser_engine import BrowserEngine, BrowserWorkflowError, WorkflowResult
from .skills import Outcome, SkillContext, SkillError, skill

# One engine per controller. Skills are dispatched one at a time against the
# session's single controller, so caching here keeps the DevTools connection
# and popup state alive across calls instead of re-attaching every skill.
_engines: "dict[int, BrowserEngine]" = {}


def _engine(ctx: SkillContext) -> BrowserEngine:
    """The BrowserEngine bound to this context's controller."""
    key = id(ctx.controller)
    engine = _engines.get(key)
    if engine is None:
        engine = BrowserEngine(controller=ctx.controller)
        _engines[key] = engine
    return engine


def reset_engines() -> None:
    """Drop cached engines (session teardown and tests)."""
    _engines.clear()


def bind_engine(controller: Any, engine: BrowserEngine) -> None:
    """Pre-bind a BrowserEngine to a controller.

    Lets a caller (notably the test suite) supply an engine with injected
    drivers, so browser skills can be exercised without a live browser.
    """
    _engines[id(controller)] = engine


def _run(ctx: SkillContext, action, description: str) -> Outcome:
    """Execute a workflow and translate it into a verifiable Outcome.

    Every browser skill funnels through here so failure handling, state
    recording, and the expectation contract are identical across the catalog.
    """
    try:
        result: WorkflowResult = action()
    except BrowserWorkflowError as exc:
        # A workflow failure is already human-readable and actionable; surface
        # it as a SkillError so the dispatcher can log and replan on it.
        raise SkillError(str(exc))
    ctx.state.record_action(description)
    if result.url:
        ctx.state.set_browser_url(result.url)
    return Outcome(result.detail, result.expected, app_target="browser")


def _query(params: dict[str, Any]) -> str:
    """The search phrase, accepting the names an AI naturally reaches for."""
    for key in ("query", "q", "search", "text", "title", "song"):
        value = str(params.get(key, "")).strip()
        if value:
            return value
    raise SkillError("this skill requires a 'query' parameter")


# --- search ------------------------------------------------------------------

@skill(
    "youtube_search",
    "Search YouTube for a query. Opens the results page and clears any consent "
    "or sign-in popups automatically.",
    PermissionLevel.DESKTOP,
    {"query": "what to search for on YouTube"},
)
def youtube_search(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    query = _query(params)
    return _run(
        ctx, lambda: _engine(ctx).youtube_search(query), f"searched YouTube for {query}"
    )


@skill(
    "youtube_play",
    "Play a song or video on YouTube by name — the complete workflow. Finds the "
    "best match, opens it, dismisses popups, and starts playback. Use this "
    "instead of searching and then clicking a result.",
    PermissionLevel.DESKTOP,
    {"query": "the song, video, or channel to play"},
)
def youtube_play(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    query = _query(params)
    return _run(ctx, lambda: _engine(ctx).youtube_play(query), f"played {query} on YouTube")


@skill(
    "google_search",
    "Search Google for a query in the default browser.",
    PermissionLevel.DESKTOP,
    {"query": "what to search for"},
)
def google_search(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    query = _query(params)
    return _run(
        ctx, lambda: _engine(ctx).google_search(query), f"searched Google for {query}"
    )


@skill(
    "web_search",
    "Search the web in the default browser (google, bing, duckduckgo, youtube).",
    PermissionLevel.DESKTOP,
    {"query": "search terms", "engine": "(optional) google|bing|duckduckgo|youtube"},
)
def web_search(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    query = _query(params)
    engine_name = str(params.get("engine", "google")).strip().lower() or "google"
    return _run(
        ctx,
        lambda: _engine(ctx).search(query, engine_name),
        f"searched {engine_name} for {query}",
    )


# --- navigation --------------------------------------------------------------

@skill(
    "open_url",
    "Open a web address in the default browser and confirm the page loaded.",
    PermissionLevel.DESKTOP,
    {"url": "the address to open", "new_tab": "(optional) true to use a new tab"},
)
def open_url(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    url = str(params.get("url") or params.get("address") or "").strip()
    if not url:
        raise SkillError("open_url requires a 'url' parameter")
    new_tab = _flag(params, "new_tab")
    return _run(
        ctx, lambda: _engine(ctx).open_url(url, new_tab=new_tab), f"opened {url}"
    )


@skill(
    "new_tab",
    "Open a new browser tab, optionally at a URL.",
    PermissionLevel.DESKTOP,
    {"url": "(optional) address to open in the new tab"},
)
def new_tab(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    url = str(params.get("url", "")).strip() or "about:blank"
    return _run(ctx, lambda: _engine(ctx).new_tab(url), "opened a new browser tab")


@skill(
    "close_tab",
    "Close the active browser tab.",
    PermissionLevel.DESKTOP,
)
def close_tab(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    return _run(ctx, lambda: _engine(ctx).close_tab(), "closed a browser tab")


@skill(
    "switch_tab",
    "Switch to another browser tab, by part of its title or URL (or the next "
    "tab when no target is given).",
    PermissionLevel.DESKTOP,
    {"target": "(optional) part of the tab's title or address"},
)
def switch_tab(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    target = str(params.get("target") or params.get("title") or params.get("url") or "").strip()
    return _run(
        ctx,
        lambda: _engine(ctx).switch_tab(target),
        f"switched to tab {target}" if target else "switched browser tab",
    )


@skill("back", "Go back one page in the browser's history.", PermissionLevel.DESKTOP)
def back(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    return _run(ctx, lambda: _engine(ctx).back(), "went back in the browser")


@skill("forward", "Go forward one page in the browser's history.", PermissionLevel.DESKTOP)
def forward(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    return _run(ctx, lambda: _engine(ctx).forward(), "went forward in the browser")


@skill("refresh", "Reload the current browser page.", PermissionLevel.DESKTOP)
def refresh(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    return _run(ctx, lambda: _engine(ctx).refresh(), "refreshed the browser page")


# --- information -------------------------------------------------------------

@skill(
    "browser_page",
    "Report the title and address of the page currently open in the browser.",
    PermissionLevel.DESKTOP,
)
def browser_page(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    return _run(ctx, lambda: _engine(ctx).page_info(), "read the current browser page")


@skill(
    "which_browser",
    "Report which browser is the system default.",
    PermissionLevel.DESKTOP,
)
def which_browser(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    return Outcome(f"default browser: {_engine(ctx).which_browser()}")


@skill(
    "launch_browser",
    "Open the user's default browser, optionally at a URL.",
    PermissionLevel.DESKTOP,
    {"url": "(optional) URL to open"},
)
def launch_browser(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    url = str(params.get("url", "")).strip()
    if not url or url == "about:blank":
        return _run(ctx, lambda: _engine(ctx).new_tab("about:blank"), "opened the browser")
    return _run(ctx, lambda: _engine(ctx).open_url(url), f"opened browser at {url}")


def _flag(params: dict[str, Any], key: str) -> bool:
    return str(params.get(key, "")).strip().lower() in ("true", "1", "yes", "on")
