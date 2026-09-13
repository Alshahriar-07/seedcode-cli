"""Operator skills: screen intelligence, semantic UI, apps, web, memory.

The Phase-4+ operator layers exposed to the AI as permissioned skills, in
the same registry as the built-in catalog (:mod:`.catalog`). Each skill is
a thin, deterministic adapter:

* queries return **compact structured data** (ids, names, counts) — never
  raw trees or screenshots;
* actions are **id/description-addressed** — the AI never supplies
  coordinates, keystrokes, or selectors;
* failures surface as model-readable text (the structured error taxonomy's
  messages), so the planner can re-plan; nothing here can terminate the
  process (lifecycle) or touch SeedCode's own console (selfguard — the
  underlying windows driver already refuses).

Importing this module registers the skills; :mod:`.catalog` imports it.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.errors import ApplicationNotFoundError, OperatorError
from ..tools.permissions import PermissionLevel
from .permissions import CATEGORY_INSTALL
from .skills import Outcome, SkillContext, SkillError, skill


def _compact(value: Any, limit: int = 3500) -> str:
    """Compact JSON rendering with a size bound for model-facing output."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + " …[truncated]"


# --- screen intelligence queries ------------------------------------------------

@skill(
    "screen_state",
    "Structured screen state: active window, windows, monitors, and UI "
    "elements with semantic element ids. Prefer over screenshots.",
    PermissionLevel.DESKTOP,
    {"include_elements": "(optional) false to omit the element list"},
)
def screen_state(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .screen_state import get_screen_engine

    include_elements = str(params.get("include_elements", "true")).lower() not in (
        "false", "0", "no"
    )
    state = get_screen_engine().state_json(include_elements=include_elements)
    return Outcome(_compact(state))


@skill(
    "get_active_window",
    "The currently focused window with app name, title, pid, and bounds.",
    PermissionLevel.DESKTOP,
)
def get_active_window(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .screen_state import get_screen_engine

    win = get_screen_engine().active_window()
    if win is None:
        return Outcome("no focused window detected")
    return Outcome(_compact({
        "id": win.id, "app": _app_of(win.title), "title": win.title,
        "pid": win.pid, "bounds": win.bounds,
    }))


@skill(
    "list_windows",
    "All visible top-level windows with app, title, pid, and focus state.",
    PermissionLevel.DESKTOP,
)
def list_windows_query(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .screen_state import get_screen_engine

    wins = get_screen_engine().list_windows()
    return Outcome(_compact([
        {"id": w.id, "app": _app_of(w.title), "title": w.title, "pid": w.pid,
         "focused": w.focused, "minimized": w.minimized}
        for w in wins
    ]))


@skill(
    "find_ui_element",
    "Find a UI element by name/role (e.g. \"Play button\") and return its "
    "semantic element id. The id is what click/type actions take.",
    PermissionLevel.DESKTOP,
    {"query": "name or description, e.g. 'Play' or 'search box'",
     "window": "(optional) window title fragment to scan"},
)
def find_ui_element(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .screen_state import get_screen_engine

    query = str(params.get("query", "")).strip()
    if not query:
        raise SkillError("find_ui_element requires a 'query' parameter")
    window = str(params.get("window", "")).strip() or None
    el = get_screen_engine().find_element(query, window=window, fresh=True)
    return Outcome(_compact({
        "id": el.id, "role": el.role, "name": el.name, "enabled": el.enabled,
    }))


@skill(
    "get_focused_element",
    "The element in the active window that currently has keyboard focus.",
    PermissionLevel.DESKTOP,
)
def get_focused_element(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .screen_state import get_screen_engine

    engine = get_screen_engine()
    # The UIA tree does not expose focus state per element in the light
    # walker; report the focused window plus its first focusable candidate.
    win = engine.active_window()
    if win is None:
        return Outcome("no focused window")
    state = engine.state_json(include_elements=True)
    elements = state.get("elements", [])
    return Outcome(_compact({
        "window": {"id": win.id, "title": win.title},
        "elements": elements[:30],
    }))


def _app_of(title: str) -> str:
    for sep in (" — ", " - ", " – "):
        if sep in title:
            return title.rsplit(sep, 1)[-1].strip() or title.strip()
    return title.strip()


# --- semantic element actions ---------------------------------------------------

@skill(
    "click_element",
    "Click a UI element by its semantic element id (from find_ui_element).",
    PermissionLevel.DESKTOP,
    {"element_id": "element id from a screen query", "double": "(optional) true to double-click"},
)
def click_element(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .semantic import SemanticActions

    element_id = _require_id(params)
    double = str(params.get("double", "")).lower() in ("true", "1", "yes")
    actions = SemanticActions()
    detail = actions.click(element_id, double=double)
    ctx.state.record_action(detail)
    return Outcome(detail, {"element": element_id})


@skill(
    "type_into_element",
    "Type text into a UI element by element id (focuses it first).",
    PermissionLevel.DESKTOP,
    {"element_id": "element id from a screen query", "text": "text to type",
     "secret": "(optional) true for passwords"},
)
def type_into_element(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .semantic import SemanticActions

    element_id = _require_id(params)
    text = str(params.get("text", ""))
    if not text:
        raise SkillError("type_into_element requires 'text'")
    secret = str(params.get("secret", "")).lower() in ("true", "1", "yes")
    actions = SemanticActions()
    detail = actions.type_into(element_id, text, secret=secret)
    ctx.state.record_action(detail)
    return Outcome(detail, {"element": element_id})


@skill(
    "focus_element",
    "Move keyboard focus to a UI element by element id.",
    PermissionLevel.DESKTOP,
    {"element_id": "element id from a screen query"},
)
def focus_element(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .semantic import SemanticActions

    detail = SemanticActions().focus(_require_id(params))
    ctx.state.record_action(detail)
    return Outcome(detail)


@skill(
    "press_element",
    "Focus an element by id, then send a keyboard shortcut to it.",
    PermissionLevel.DESKTOP,
    {"element_id": "element id from a screen query",
     "keys": "e.g. \"enter\" or \"ctrl+s\""},
)
def press_element(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .semantic import SemanticActions

    element_id = _require_id(params)
    keys = [k.strip() for k in str(params.get("keys", "")).replace("+", " ").split() if k.strip()]
    if not keys:
        raise SkillError("press_element requires 'keys'")
    detail = SemanticActions().press(element_id, keys)
    ctx.state.record_action(detail)
    return Outcome(detail)


def _require_id(params: dict[str, Any]) -> str:
    element_id = str(params.get("element_id", "")).strip()
    if not element_id:
        raise SkillError("this action requires an 'element_id' from a screen query")
    return element_id


# --- application controller -------------------------------------------------------

@skill(
    "app_open",
    "Open an application by name: reuses/focuses a running instance, "
    "launches via Start Menu shortcut or registered executable, then "
    "verifies a window appeared. Errors list trusted install options.",
    PermissionLevel.DESKTOP,
    {"target": "application name, e.g. 'Spotify' or 'Calculator'"},
)
def app_open(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from ..apps.launcher import launch_app

    target = str(params.get("target", "")).strip()
    if not target:
        raise SkillError("app_open requires a 'target' parameter")
    try:
        result = launch_app(target)
    except ApplicationNotFoundError as exc:
        # Missing app: surface the install option; installation is a
        # separate, separately-confirmed skill (install_app).
        raise SkillError(
            f"{exc} Ask the user whether to install it (install_app)."
        )
    ctx.state.record_action(result.describe())
    return Outcome(result.describe())


@skill(
    "app_find",
    "Check whether an application is installed and how it would be launched.",
    PermissionLevel.DESKTOP,
    {"target": "application name"},
)
def app_find(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from ..apps.discovery import find_app

    target = str(params.get("target", "")).strip()
    if not target:
        raise SkillError("app_find requires a 'target' parameter")
    try:
        app = find_app(target)
    except ApplicationNotFoundError as exc:
        raise SkillError(str(exc))
    return Outcome(_compact({
        "name": app.name, "source": app.source, "target": app.target,
        "exe": app.exe, "install_location": app.install_location,
    }))


@skill(
    "app_close",
    "Close an application window by name. Refuses SeedCode's own terminal.",
    PermissionLevel.DESKTOP,
    {"target": "part of the window title"},
)
def app_close(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    target = _req(params, "target")
    ctx.controller.close_app(target)
    ctx.state.record_action(f"closed {target}")
    return Outcome(f"closed {target}", {"window_gone": target})


@skill(
    "install_app",
    "Install a missing application via a trusted source (winget). Always "
    "asks the user first with the exact package and source shown.",
    PermissionLevel.DESKTOP,
    {"target": "application name", "confirm": "(optional) skip asking (ignored)"},
)
def install_app_skill(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from ..apps.installer import install_app

    target = str(params.get("target", "")).strip()
    if not target:
        raise SkillError("install_app requires a 'target' parameter")

    # The INSTALL category is sensitive: the gate re-asks every time and the
    # model cannot mark anything trusted (installer.py refuses unknown
    # sources). The confirm callback is the session's desktop gate.
    def _confirm(plan_text: str) -> bool:
        try:
            ctx.permissions.desktop.check(CATEGORY_INSTALL, plan_text)  # type: ignore[union-attr]
            return True
        except Exception:
            return False

    result = install_app(target, confirm=_confirm)
    ctx.state.record_action(result.detail)
    if not result.success:
        raise SkillError(result.detail)
    return Outcome(result.detail)


def _req(params: dict[str, Any], key: str) -> str:
    val = str(params.get(key, "")).strip()
    if not val:
        raise SkillError(f"skill requires a '{key}' parameter")
    return val


# --- web intelligence -----------------------------------------------------------

@skill(
    "web_page_info",
    "Current page url and title from the browser's DOM.",
    PermissionLevel.DESKTOP,
)
def web_page_info(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .browser_extract import get_web_extractor

    try:
        info = get_web_extractor().page_info()
    except OperatorError as exc:
        raise SkillError(str(exc))
    return Outcome(_compact(info))


@skill(
    "web_extract_text",
    "Extract the page's readable text (DOM, no screenshots).",
    PermissionLevel.DESKTOP,
)
def web_extract_text(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .browser_extract import get_web_extractor

    try:
        extraction = get_web_extractor().page_text()
    except OperatorError as exc:
        raise SkillError(str(exc))
    return Outcome(_compact(extraction.to_dict()))


@skill(
    "web_extract_headings",
    "Extract the page's headings (h1-h6) with their levels.",
    PermissionLevel.DESKTOP,
)
def web_extract_headings(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .browser_extract import get_web_extractor

    try:
        extraction = get_web_extractor().headings()
    except OperatorError as exc:
        raise SkillError(str(exc))
    return Outcome(_compact(extraction.to_dict()))


@skill(
    "web_extract_links",
    "Extract visible links; optionally filter by a text fragment.",
    PermissionLevel.DESKTOP,
    {"filter": "(optional) keep links whose text/href contains this"},
)
def web_extract_links(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .browser_extract import get_web_extractor

    try:
        extraction = get_web_extractor().links(
            filter_text=str(params.get("filter", "")).strip()
        )
    except OperatorError as exc:
        raise SkillError(str(exc))
    return Outcome(_compact(extraction.to_dict()))


@skill(
    "web_extract_tables",
    "Extract every table on the page as rows of cells (DOM).",
    PermissionLevel.DESKTOP,
)
def web_extract_tables(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .browser_extract import get_web_extractor

    try:
        extraction = get_web_extractor().tables()
    except OperatorError as exc:
        raise SkillError(str(exc))
    return Outcome(_compact(extraction.to_dict()))


@skill(
    "web_extract_lists",
    "Extract list structures (ul/ol) with their items.",
    PermissionLevel.DESKTOP,
)
def web_extract_lists(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .browser_extract import get_web_extractor

    try:
        extraction = get_web_extractor().lists()
    except OperatorError as exc:
        raise SkillError(str(exc))
    return Outcome(_compact(extraction.to_dict()))


@skill(
    "web_extract_metadata",
    "Extract page metadata: description, og tags, canonical url, language.",
    PermissionLevel.DESKTOP,
)
def web_extract_metadata(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .browser_extract import get_web_extractor

    try:
        extraction = get_web_extractor().metadata()
    except OperatorError as exc:
        raise SkillError(str(exc))
    return Outcome(_compact(extraction.to_dict()))


@skill(
    "web_extract_structured",
    "Extract JSON-LD structured data (products, articles, recipes...).",
    PermissionLevel.DESKTOP,
)
def web_extract_structured(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .browser_extract import get_web_extractor

    try:
        extraction = get_web_extractor().structured_data()
    except OperatorError as exc:
        raise SkillError(str(exc))
    return Outcome(_compact(extraction.to_dict()))


@skill(
    "web_find",
    "Find where a phrase appears on the page, with surrounding context.",
    PermissionLevel.DESKTOP,
    {"query": "text to locate on the page"},
)
def web_find(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .browser_extract import get_web_extractor

    query = str(params.get("query", "")).strip()
    if not query:
        raise SkillError("web_find requires a 'query' parameter")
    try:
        extraction = get_web_extractor().find_on_page(query)
    except OperatorError as exc:
        raise SkillError(str(exc))
    return Outcome(_compact(extraction.to_dict()))


@skill(
    "web_wait_for",
    "Wait (bounded, state-based) until a CSS selector exists and is visible.",
    PermissionLevel.DESKTOP,
    {"selector": "CSS selector to wait for",
     "timeout": "(optional) seconds (max 30)"},
)
def web_wait_for(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from .browser_extract import get_web_extractor

    selector = str(params.get("selector", "")).strip()
    if not selector:
        raise SkillError("web_wait_for requires a 'selector' parameter")
    try:
        timeout = float(params.get("timeout", 8.0) or 8.0)
    except (TypeError, ValueError):
        timeout = 8.0
    try:
        ok = get_web_extractor().wait_for_element(selector, timeout_s=timeout)
    except OperatorError as exc:
        raise SkillError(str(exc))
    if not ok:
        raise SkillError(f'"{selector}" did not appear within {timeout:g}s.')
    return Outcome(f'"{selector}" is present and visible')


# --- memory ----------------------------------------------------------------------

@skill(
    "memory_save",
    "Persist a structured note into local memory (web findings, app usage, "
    "preferences). Secret-like fields are refused.",
    PermissionLevel.WORKSPACE,
    {"namespace": "sessions | desktop | user | web | files",
     "id": "record id, e.g. 'app:spotify'", "summary": "one-line summary",
     "data": "(optional) JSON object to store",
     "tags": "(optional) comma-separated tags"},
)
def memory_save(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from ..core.errors import SecurityError
    from ..memory.store import memory_store

    namespace = str(params.get("namespace", "")).strip()
    record_id = str(params.get("id", "")).strip()
    summary = str(params.get("summary", "")).strip()
    if not namespace or not record_id or not summary:
        raise SkillError("memory_save requires 'namespace', 'id', and 'summary'")
    data = params.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data) if data.strip() else {}
        except ValueError:
            raise SkillError("'data' must be a JSON object")
    if data is not None and not isinstance(data, dict):
        raise SkillError("'data' must be a JSON object")
    tags = [t.strip() for t in str(params.get("tags", "")).split(",") if t.strip()]
    try:
        ok = memory_store().put(namespace, record_id, summary, data, tags)
    except SecurityError as exc:
        raise SkillError(str(exc))
    if not ok:
        raise SkillError("memory write failed (disk error)")
    return Outcome(f"saved {namespace}/{record_id}")


@skill(
    "memory_search",
    "Search local memory indexes (compact results; load records with "
    "memory_get). Use for \"what did I...\" questions.",
    PermissionLevel.WORKSPACE,
    {"query": "text to look for", "namespace": "(optional) restrict to one namespace",
     "tags": "(optional) comma-separated tags"},
)
def memory_search(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from ..memory.store import memory_store

    query = str(params.get("query", "")).strip()
    namespace = str(params.get("namespace", "")).strip()
    tags = [t.strip() for t in str(params.get("tags", "")).split(",") if t.strip()]
    store = memory_store()
    namespaces = [namespace] if namespace else list(store.namespaces()) or ["sessions"]
    results: dict[str, Any] = {}
    for ns in namespaces:
        try:
            hits = store.query(ns, text=query, tags=tags, limit=8)
        except ValueError:
            continue
        if hits:
            results[ns] = hits
    return Outcome(_compact(results) if results else "no matching memory records")


@skill(
    "memory_get",
    "Load one memory record by namespace and id.",
    PermissionLevel.WORKSPACE,
    {"namespace": "the record's namespace", "id": "the record id"},
)
def memory_get(ctx: SkillContext, params: dict[str, Any]) -> Outcome:
    from ..memory.store import memory_store

    namespace = str(params.get("namespace", "")).strip()
    record_id = str(params.get("id", "")).strip()
    if not namespace or not record_id:
        raise SkillError("memory_get requires 'namespace' and 'id'")
    record = memory_store().get(namespace, record_id)
    if record is None:
        raise SkillError(f"no record '{record_id}' in '{namespace}'")
    return Outcome(_compact({
        "id": record.id, "summary": record.summary, "tags": record.tags,
        "created_at": record.created_at, "data": record.data,
    }))
