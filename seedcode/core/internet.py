"""Modular internet retrieval for Chat Mode and Agent Mode (v9.1.1).

Both modes can obtain fresh external information when a task genuinely needs
it. The architecture is deliberately small and provider-agnostic so it works
the same for a local model and a remote one:

    request -> Intent says "needs current information" -> search -> read the
    best sources -> a bounded context block the model reasons over -> answer

Nothing here browses the internet on its own: a caller asks, and only the
:mod:`seedcode.core.intent` classifier (or an explicit agent tool call)
decides that it should.

Design rules:

* **No new dependency.** HTTP uses ``httpx`` (already required) and parsing
  uses the standard library.
* **Never fatal.** A network error, an unparseable page or a blocked host
  returns an empty result with a short reason — it can never fail a turn.
* **Bounded.** Result count, per-page size and the total context budget are
  capped, so retrieval cannot flood the model's context window.
* **Testable.** Every network call goes through :func:`_http_get`, a single
  seam a test can patch, so the suite never touches the network.
* **Private.** The search query is the only thing sent out; no file contents,
  no configuration, no credentials.
"""

from __future__ import annotations

import html as _html
import os
import re
import urllib.parse
from dataclasses import dataclass, field

__all__ = [
    "SearchResult",
    "ResearchResult",
    "context_block",
    "fetch",
    "internet_disabled",
    "research",
    "search",
]

# A conservative browser-ish UA: the default python-httpx UA is refused by some
# hosts, and we are reading public pages (never authenticating).
_USER_AGENT = (
    "Mozilla/5.0 (compatible; SeedCode/9.1.1; +https://github.com/Alshahriar-07/seedcode-cli)"
)

# DuckDuckGo's no-JS HTML endpoint: stable, keyless, and tolerant of automation.
_SEARCH_URL = "https://html.duckduckgo.com/html/"

_TIMEOUT_S = 10.0
_MAX_PAGE_CHARS = 6000
_DEFAULT_RESULTS = 5

# Environment switch so an offline/air-gapped install can turn retrieval off.
_DISABLE_ENV = "SEEDCODE_DISABLE_INTERNET"


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One search hit."""

    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """The outcome of one lookup (possibly empty, never an exception)."""

    query: str
    results: tuple[SearchResult, ...] = ()
    excerpts: tuple[tuple[str, str], ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.results or self.excerpts)


def internet_disabled() -> bool:
    """Whether retrieval is switched off for this process."""
    return (os.environ.get(_DISABLE_ENV) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


# --- HTTP seam ---------------------------------------------------------------
def _http_get(url: str, *, timeout: float = _TIMEOUT_S) -> str:
    """Fetch a URL as text. The single network seam (patch this in tests)."""
    import httpx

    response = httpx.get(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "en"},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


# --- HTML helpers ------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")


def _strip_html(markup: str) -> str:
    """Visible text from an HTML fragment (tags, scripts and entities gone)."""
    text = _SCRIPT_RE.sub(" ", markup)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    text = _WS_RE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    return _BLANK_RE.sub("\n\n", "\n".join(lines)).strip()


def _ddg_results(page: str, limit: int) -> list[SearchResult]:
    """Parse results out of DuckDuckGo's HTML endpoint."""
    results: list[SearchResult] = []
    # Each result is an anchor with class result__a; the snippet follows in a
    # nearby result__snippet anchor. Parse by block to keep the pairing.
    blocks = re.split(r'class="result__body"', page)
    for block in blocks[1:]:
        link = re.search(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )
        if not link:
            continue
        url = _html.unescape(link.group(1))
        url = _unwrap_ddg_url(url)
        title = _strip_html(link.group(2))
        snippet_match = re.search(
            r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL
        )
        snippet = _strip_html(snippet_match.group(1)) if snippet_match else ""
        if title and url:
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def _unwrap_ddg_url(url: str) -> str:
    """DuckDuckGo wraps outbound links; recover the real target."""
    if "duckduckgo.com/l/" in url or url.startswith("//duckduckgo.com/l/"):
        parsed = urllib.parse.urlparse(url if "://" in url else "https:" + url)
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    if url.startswith("//"):
        return "https:" + url
    return url


# --- public API --------------------------------------------------------------
def search(query: str, *, limit: int = _DEFAULT_RESULTS) -> list[SearchResult]:
    """Search the web; returns [] on any failure (never raises)."""
    query = (query or "").strip()
    if not query or internet_disabled():
        return []
    limit = max(1, min(int(limit), 10))
    try:
        page = _http_get(_SEARCH_URL + "?" + urllib.parse.urlencode({"q": query}))
    except Exception:
        return []
    try:
        return _ddg_results(page, limit)
    except Exception:
        return []


def fetch(url: str, *, max_chars: int = _MAX_PAGE_CHARS) -> str:
    """Fetch a URL and return its readable text ('' on failure)."""
    if not url or internet_disabled():
        return ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return ""
    try:
        markup = _http_get(url)
    except Exception:
        return ""
    text = _strip_html(markup)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + " …"
    return text


def research(
    query: str,
    *,
    limit: int = _DEFAULT_RESULTS,
    read_top: int = 2,
    max_source_chars: int = 2500,
) -> ResearchResult:
    """Search, then read the top few sources into bounded excerpts."""
    query = (query or "").strip()
    if not query:
        return ResearchResult(query=query, error="empty query")
    if internet_disabled():
        return ResearchResult(query=query, error="internet access is disabled")

    results = search(query, limit=limit)
    if not results:
        return ResearchResult(
            query=query, error="no results found (offline, or the search was blocked)"
        )

    excerpts: list[tuple[str, str]] = []
    for hit in results[: max(0, read_top)]:
        text = fetch(hit.url, max_chars=max_source_chars)
        if text:
            excerpts.append((hit.url, text))
    return ResearchResult(query=query, results=tuple(results), excerpts=tuple(excerpts))


def context_block(result: ResearchResult, *, char_budget: int = 5000) -> str:
    """Render a research result as a bounded block for the model.

    Compact and attributed: each source keeps its URL so the model can cite it.
    ``char_budget`` caps the whole block; the caller chooses how much of its
    context to spend on external information.
    """
    if not result.ok:
        return ""
    lines: list[str] = [
        "[WEB RESULTS] The user's request may need information newer than your "
        "training data. The following was retrieved now; use it and cite the URLs.",
        f"Query: {result.query}",
    ]
    if result.excerpts:
        for url, text in result.excerpts:
            lines.append(f"\n--- Source: {url} ---\n{text}")
    else:
        for hit in result.results:
            snippet = hit.snippet or "(no snippet)"
            lines.append(f"\n- {hit.title} ({hit.url}): {snippet}")

    block = "\n".join(lines)
    if len(block) > char_budget:
        suffix = "\n… [retrieval truncated]"
        keep = max(0, char_budget - len(suffix))
        block = block[:keep].rstrip() + suffix
    return block
