"""Web extraction: structured page data over the DevTools DOM tier.

Turns "what does this page say?" into a deterministic JSON answer using the
page's own DOM (:mod:`.browser_cdp`), never screenshots. Every extraction
carries source traceability (url, title, timestamp, method) so answers can
cite where they came from — and so the memory layer can store provenance.

Synchronization is state-based: ``wait_for_element`` /
``wait_for_page_state`` poll the live DOM with bounded timeouts; there are
no blind sleeps. All JS evaluation is injectable, making the module fully
unit-testable without a browser.

The one intentional limitation: without a DevTools connection (browser not
started with the debugging port), DOM extraction is unavailable and
:class:`WebPageNotConnectedError` says so — falling back to reading pixels
would be the exact anti-pattern this module exists to remove.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import ExtractionError, WebPageNotConnectedError
from ..core.limits import MAX_WAIT_ELEMENT_S, MAX_WAIT_NAVIGATION_S, MAX_WAIT_POLL_S, clamp_wait


@dataclass(slots=True)
class PageSource:
    """Provenance record attached to every extraction."""

    url: str
    title: str
    retrieved_at: str
    method: str = "dom"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_url": self.url, "page_title": self.title,
            "retrieved_at": self.retrieved_at, "extraction_method": self.method,
        }


@dataclass(slots=True)
class Extraction:
    """One structured extraction with its source."""

    kind: str                      # text | headings | links | tables | metadata | find
    data: Any
    source: PageSource

    def to_dict(self) -> dict[str, Any]:
        out = {"kind": self.kind, "content": self.data}
        out.update(self.source.to_dict())
        return out


# --- JS payloads (run inside the page; return JSON-safe values) ---------------------

_JS_TEXT = """(() => {
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll('script,style,noscript,svg').forEach(n => n.remove());
  return (clone.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim().slice(0, 20000);
})()"""

_JS_HEADINGS = """(() => {
  const out = [];
  document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(h => {
    const r = h.getBoundingClientRect();
    if (r.width <= 0 && r.height <= 0) return;
    out.push({level: parseInt(h.tagName.slice(1)), text: (h.innerText || '').trim()});
  });
  return out.slice(0, 100);
})()"""

_JS_LINKS = """(() => {
  const out = [];
  document.querySelectorAll('a[href]').forEach(a => {
    const r = a.getBoundingClientRect();
    if (r.width <= 0 && r.height <= 0) return;
    out.push({text: (a.innerText || '').trim().slice(0, 200), href: a.href});
  });
  return out.slice(0, 300);
})()"""

_JS_TABLES = """(() => {
  const tables = [];
  document.querySelectorAll('table').forEach(t => {
    const rows = [];
    t.querySelectorAll('tr').forEach(tr => {
      const cells = [];
      tr.querySelectorAll('th,td').forEach(td =>
        cells.push((td.innerText || '').trim().slice(0, 200)));
      if (cells.length) rows.push(cells);
    });
    if (rows.length) tables.push(rows.slice(0, 200));
  });
  return tables.slice(0, 30);
})()"""

_JS_LISTS = """(() => {
  const lists = [];
  document.querySelectorAll('ul,ol').forEach(l => {
    const items = [];
    l.querySelectorAll(':scope > li').forEach(li =>
      items.push((li.innerText || '').trim().slice(0, 300)));
    if (items.length) lists.push(items.slice(0, 100));
  });
  return lists.slice(0, 50);
})()"""

_JS_META = """(() => {
  const pick = (sel, attr) => {
    const el = document.querySelector(sel);
    return el ? (el.getAttribute(attr) || el.content || '') : '';
  };
  return {
    description: pick('meta[name="description"]', 'content')
      || pick('meta[property="og:description"]', 'content'),
    og_title: pick('meta[property="og:title"]', 'content'),
    og_image: pick('meta[property="og:image"]', 'content'),
    canonical: pick('link[rel="canonical"]', 'href'),
    lang: document.documentElement.lang || '',
    charset: document.characterSet || '',
  };
})()"""

_JS_FIND = """((needle) => {
  const want = needle.toLowerCase();
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode()) && out.length < 40) {
    const text = (node.textContent || '').trim();
    if (!text) continue;
    const idx = text.toLowerCase().indexOf(want);
    if (idx === -1) continue;
    let el = node.parentElement;
    while (el && el !== document.body) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 || r.height > 0) break;
      el = el.parentElement;
    }
    const a = el && el.closest ? el.closest('a') : null;
    out.push({
      text: text.slice(0, 300),
      tag: el ? el.tagName.toLowerCase() : '',
      href: a ? a.href : null,
    });
  }
  return out;
})(%(needle)s)"""

_JS_WAIT_ELEMENT = """((selector) => {
  const el = document.querySelector(selector);
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 || r.height > 0;
})(%(selector)s)"""


class WebExtractor:
    """DOM-level page extraction (injectable evaluate for tests)."""

    def __init__(self, cdp: Any = None) -> None:
        if cdp is None:
            from .computer import browser_cdp as cdp  # type: ignore
        self._cdp = cdp

    # --- plumbing ---------------------------------------------------------------
    def _require_live(self) -> None:
        try:
            live = bool(self._cdp.is_available())
        except Exception:
            live = False
        if not live:
            raise WebPageNotConnectedError()

    def _eval(self, js: str) -> Any:
        try:
            return self._cdp.evaluate(js)
        except Exception as exc:
            raise ExtractionError(f"page evaluation failed: {exc}")

    def _source(self) -> PageSource:
        url = self._eval("location.href") or ""
        title = self._eval("document.title") or ""
        return PageSource(
            url=str(url), title=str(title),
            retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )

    # --- extractions ---------------------------------------------------------------
    def page_text(self) -> Extraction:
        self._require_live()
        return Extraction("text", self._eval(_JS_TEXT) or "", self._source())

    def headings(self) -> Extraction:
        self._require_live()
        return Extraction("headings", self._eval(_JS_HEADINGS) or [], self._source())

    def links(self, *, filter_text: str = "") -> Extraction:
        self._require_live()
        data = self._eval(_JS_LINKS) or []
        if filter_text:
            low = filter_text.lower()
            data = [l for l in data if low in (l.get("text") or "").lower()
                    or low in (l.get("href") or "").lower()]
        return Extraction("links", data, self._source())

    def tables(self) -> Extraction:
        self._require_live()
        return Extraction("tables", self._eval(_JS_TABLES) or [], self._source())

    def lists(self) -> Extraction:
        self._require_live()
        return Extraction("lists", self._eval(_JS_LISTS) or [], self._source())

    def metadata(self) -> Extraction:
        self._require_live()
        return Extraction("metadata", self._eval(_JS_META) or {}, self._source())

    def structured_data(self) -> Extraction:
        """JSON-LD / microdata blocks sites use for products, recipes, etc."""
        self._require_live()
        js = """(() => {
          const out = [];
          document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
            try { out.push(JSON.parse(s.textContent)); } catch (e) {}
          });
          return out.slice(0, 20);
        })()"""
        return Extraction("structured_data", self._eval(js) or [], self._source())

    def find_on_page(self, query: str) -> Extraction:
        """Locate text/elements matching a phrase, with their context."""
        self._require_live()
        needle = (query or "").strip()
        if not needle:
            raise ExtractionError("A search phrase is required.")
        import json as _json

        js = _JS_FIND % {"needle": _json.dumps(needle)}
        return Extraction("find", self._eval(js) or [], self._source())

    def page_info(self) -> dict[str, Any]:
        self._require_live()
        src = self._source()
        return {"url": src.url, "title": src.title, "method": src.method}

    # --- waits (state-based, bounded) -------------------------------------------------
    def wait_for_element(self, selector: str,
                         timeout_s: float = MAX_WAIT_ELEMENT_S) -> bool:
        """Poll until a selector exists and is visible. False on timeout."""
        self._require_live()
        import json as _json

        js = _JS_WAIT_ELEMENT % {"selector": _json.dumps(selector)}
        deadline = time.monotonic() + clamp_wait(timeout_s, 30.0)
        while time.monotonic() < deadline:
            if self._eval(js) is True:
                return True
            time.sleep(MAX_WAIT_POLL_S)
        return False

    def wait_for_navigation(self, *, timeout_s: float = MAX_WAIT_NAVIGATION_S) -> bool:
        """Wait until the document reaches 'complete' (bounded)."""
        self._require_live()
        deadline = time.monotonic() + clamp_wait(timeout_s, 30.0)
        while time.monotonic() < deadline:
            try:
                if self._eval("document.readyState === 'complete'") is True:
                    return True
            except ExtractionError:
                pass  # mid-navigation evaluations can transiently fail
            time.sleep(MAX_WAIT_POLL_S)
        return False


# --- shared instance ---------------------------------------------------------------
_EXTRACTOR: WebExtractor | None = None


def get_web_extractor(cdp: Any = None) -> WebExtractor:
    global _EXTRACTOR
    if _EXTRACTOR is None or cdp is not None:
        if _EXTRACTOR is None:
            _EXTRACTOR = WebExtractor(cdp=cdp)
    return _EXTRACTOR


def reset_web_extractor() -> None:
    global _EXTRACTOR
    _EXTRACTOR = None


__all__ = [
    "PageSource", "Extraction", "WebExtractor",
    "get_web_extractor", "reset_web_extractor",
]
