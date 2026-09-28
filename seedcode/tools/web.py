"""Web tools: let the agent obtain current external information (v9.1.1).

Registered in the ``web`` tool group so they are advertised *only* when an
intent actually needs the internet (see :mod:`seedcode.core.intent`). A coding
task never sees these tools, keeping the advertised set minimal.

Both tools are read-only (``mutates=False``) and never raise: a network
failure comes back as an unsuccessful :class:`ToolResult` the model can react
to, exactly like any other tool failure.
"""

from __future__ import annotations

from .base import ToolError, ToolResult, register


@register(
    "web_search",
    "Search the web for current information (returns titles, URLs and snippets). "
    "Use it only when the request needs information newer than your training data.",
    args={
        "query": "the search query",
        "limit": "(optional) maximum number of results, default 5",
    },
    mutates=False,
    group="web",
    types={"limit": "integer"},
)
def _web_search(permissions, args) -> ToolResult:
    from ..core import internet

    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("web_search needs a non-empty 'query'.")
    if internet.internet_disabled():
        return ToolResult(False, "Internet access is disabled for this session.")
    try:
        limit = int(args.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    results = internet.search(query, limit=limit)
    if not results:
        return ToolResult(
            False,
            f"No web results for {query!r} (offline, blocked, or nothing matched).",
        )
    lines = [f"Web results for {query!r}:"]
    for hit in results:
        snippet = f" — {hit.snippet}" if hit.snippet else ""
        lines.append(f"- {hit.title}: {hit.url}{snippet}")
    return ToolResult(True, "\n".join(lines))


@register(
    "web_fetch",
    "Fetch a web page and return its readable text. Use it to read a source "
    "found with web_search when the snippet is not enough.",
    args={
        "url": "the absolute http(s) URL to read",
        "max_chars": "(optional) maximum characters to return",
    },
    mutates=False,
    group="web",
    types={"max_chars": "integer"},
)
def _web_fetch(permissions, args) -> ToolResult:
    from ..core import internet

    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("web_fetch needs a non-empty 'url'.")
    if internet.internet_disabled():
        return ToolResult(False, "Internet access is disabled for this session.")
    try:
        max_chars = int(args.get("max_chars") or 5000)
    except (TypeError, ValueError):
        max_chars = 5000
    text = internet.fetch(url, max_chars=max(200, min(max_chars, 20000)))
    if not text:
        return ToolResult(False, f"Could not read {url} (offline or blocked).")
    return ToolResult(True, f"Content of {url}:\n\n{text}")
