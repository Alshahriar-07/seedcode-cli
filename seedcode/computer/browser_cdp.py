"""DOM inspection over the Chrome DevTools Protocol — the browser's own eyes.

This is the **DOM tier** of the detection ladder: when the user's default
browser is Chromium-based and exposes a DevTools endpoint, Seed Code can ask
the page directly ("is there a cookie banner?", "where is the first video
link?") instead of guessing from pixels. That is exact, theme-independent, and
needs no OCR.

Two deliberate design choices:

* **Opportunistic, never required.** Attaching needs the browser to have been
  started with ``--remote-debugging-port``. When it wasn't — the common case
  for an already-running browser — every function here returns ``None`` or
  ``False`` and the caller falls through to the next ladder tier. Nothing in
  Seed Code *depends* on CDP being live; the URL-first workflows in
  :mod:`.browser_engine` reach their goal without touching the DOM at all.
* **No new dependencies.** Target discovery is plain HTTP over ``urllib`` and
  the DevTools socket is a ~100-line RFC 6455 client over a stdlib socket.
  Adding ``websockets``/``selenium`` to the install for this would violate the
  "works offline, nothing extra to install" contract.

The module holds no AI code and never sees a model.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

# The DevTools port Seed Code asks the browser to expose. Fixed so a browser
# launched by an earlier session is still reachable by a later one.
DEFAULT_PORT = 9222

# Everything here is best-effort against a live browser: keep timeouts short so
# a missing/hung endpoint costs the workflow a moment, not a stall.
_HTTP_TIMEOUT_S = 1.5
_WS_TIMEOUT_S = 4.0

# How long an availability verdict stays good. A hit is re-checked often enough
# to notice the browser closing; a miss is cached longer because the expensive
# case — no DevTools at all — is also the case that must stay cheap.
_AVAIL_TTL_S = 2.0
_AVAIL_MISS_TTL_S = 10.0

# port -> (available, expires_at)
_avail_cache: dict[int, tuple[bool, float]] = {}

# Guards the shared connection: skills may run from different threads and a
# DevTools socket cannot interleave request/response pairs safely.
_lock = threading.Lock()
_conn: "_Connection | None" = None


@dataclass(slots=True)
class Tab:
    """One open browser tab, as DevTools reports it."""

    target_id: str
    title: str
    url: str
    ws_url: str

    def describe(self) -> str:
        return f'"{self.title or "(untitled)"}" — {self.url}'


@dataclass(slots=True)
class DomBox:
    """A DOM element's position, already converted to screen coordinates."""

    x: int  # center, screen space
    y: int  # center, screen space
    width: int
    height: int
    text: str

    def as_tuple(self) -> tuple[int, int, int, int]:
        """(left, top, width, height) — the resolver's box convention."""
        return (self.x - self.width // 2, self.y - self.height // 2, self.width, self.height)


# --- target discovery (plain HTTP, no socket needed) -------------------------

def _endpoint(port: int, path: str) -> str:
    return f"http://127.0.0.1:{port}/json{path}"


def _http(port: int, path: str, timeout: float = _HTTP_TIMEOUT_S) -> Any:
    """GET a DevTools HTTP endpoint, returning parsed JSON or None."""
    try:
        with urllib.request.urlopen(_endpoint(port, path), timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def is_available(port: int = DEFAULT_PORT) -> bool:
    """Whether a DevTools endpoint is answering on ``port``.

    The verdict is cached briefly. Callers like the popup manager ask this
    (directly or via ``exists``) dozens of times per sweep, and without a cache
    every miss costs a full connection timeout — turning an "is the page
    clean?" check into a multi-second stall on the overwhelmingly common
    no-DevTools machine.
    """
    global _avail_cache
    now = time.monotonic()
    cached = _avail_cache.get(port)
    if cached is not None and now < cached[1]:
        return cached[0]
    ok = _http(port, "/version") is not None
    ttl = _AVAIL_TTL_S if ok else _AVAIL_MISS_TTL_S
    _avail_cache[port] = (ok, now + ttl)
    return ok


def invalidate_availability() -> None:
    """Forget the cached availability verdict (browser started/stopped)."""
    _avail_cache.clear()


def list_tabs(port: int = DEFAULT_PORT) -> list[Tab]:
    """Every open page tab (DevTools also lists workers/extensions; skipped)."""
    data = _http(port, "/list")
    if not isinstance(data, list):
        return []
    tabs = []
    for entry in data:
        if not isinstance(entry, dict) or entry.get("type") != "page":
            continue
        tabs.append(
            Tab(
                target_id=str(entry.get("id", "")),
                title=str(entry.get("title", "")),
                url=str(entry.get("url", "")),
                ws_url=str(entry.get("webSocketDebuggerUrl", "")),
            )
        )
    return tabs


def active_tab(port: int = DEFAULT_PORT) -> Tab | None:
    """The tab DevTools lists first — the one most recently in the foreground."""
    tabs = list_tabs(port)
    return tabs[0] if tabs else None


def new_tab(url: str = "about:blank", port: int = DEFAULT_PORT) -> Tab | None:
    """Open a tab via DevTools. Returns None when the endpoint is absent."""
    data = _http(port, f"/new?{url}")
    if not isinstance(data, dict):
        return None
    _reset_connection()
    return Tab(
        target_id=str(data.get("id", "")),
        title=str(data.get("title", "")),
        url=str(data.get("url", "")),
        ws_url=str(data.get("webSocketDebuggerUrl", "")),
    )


def activate_tab(target_id: str, port: int = DEFAULT_PORT) -> bool:
    """Bring a tab to the foreground."""
    ok = _http(port, f"/activate/{target_id}") is not None
    if ok:
        _reset_connection()
    return ok


def close_tab(target_id: str, port: int = DEFAULT_PORT) -> bool:
    """Close a tab."""
    ok = _http(port, f"/close/{target_id}") is not None
    if ok:
        _reset_connection()
    return ok


# --- JavaScript evaluation (the workhorse) -----------------------------------

def evaluate(expression: str, port: int = DEFAULT_PORT) -> Any:
    """Evaluate JS in the active tab and return the JSON-decoded result.

    Returns ``None`` when DevTools is unreachable or the expression threw —
    callers treat that as "the DOM tier has nothing to say" and fall through.
    """
    # Short-circuit on the cached verdict so a machine with no DevTools pays
    # one probe per sweep rather than one per query.
    if not is_available(port):
        return None
    with _lock:
        conn = _get_connection(port)
        if conn is None:
            return None
        try:
            reply = conn.call(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True,
                    # Consent banners and player controls are only clickable
                    # when the page believes a real user gesture occurred.
                    "userGesture": True,
                },
            )
        except Exception:
            _reset_connection_locked()
            return None
    if not isinstance(reply, dict):
        return None
    if reply.get("exceptionDetails"):
        return None
    return reply.get("result", {}).get("value")


def current_url(port: int = DEFAULT_PORT) -> str | None:
    """The live address of the active tab, straight from the browser."""
    tab = active_tab(port)
    if tab is not None and tab.url:
        return tab.url
    value = evaluate("location.href", port)
    return str(value) if isinstance(value, str) else None


def current_title(port: int = DEFAULT_PORT) -> str | None:
    """The live title of the active tab."""
    tab = active_tab(port)
    if tab is not None and tab.title:
        return tab.title
    value = evaluate("document.title", port)
    return str(value) if isinstance(value, str) else None


def navigate(url: str, port: int = DEFAULT_PORT) -> bool:
    """Drive the active tab to ``url`` through DevTools."""
    return bool(evaluate(f"(location.assign({json.dumps(url)}), true)", port))


def wait_for_load(timeout_s: float = 10.0, port: int = DEFAULT_PORT) -> bool:
    """Block until the active tab finishes loading (or the timeout elapses)."""
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if evaluate("document.readyState === 'complete'", port) is True:
            return True
        time.sleep(0.25)
    return False


# --- DOM querying (the resolver's DOM tier) ----------------------------------

# Built in JS so the *page* does the matching: one round trip, and the result
# is already in screen space. Chromium exposes the window's screen origin and
# the chrome height (outerHeight - innerHeight), which together convert a
# viewport rect into a coordinate the mouse driver can click.
_BOX_JS = """
(() => {
  const el = %(finder)s;
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (!r || r.width <= 0 || r.height <= 0) return null;
  const chrome = window.outerHeight - window.innerHeight;
  return {
    x: Math.round(window.screenX + r.left + r.width / 2),
    y: Math.round(window.screenY + chrome + r.top + r.height / 2),
    width: Math.round(r.width),
    height: Math.round(r.height),
    text: (el.innerText || el.textContent || el.value || '').trim().slice(0, 120)
  };
})()
"""

# Find a visible element whose text/label/aria-label contains a phrase. Used by
# both the resolver's DOM tier and the popup manager's dismiss buttons.
_BY_TEXT_JS = """
(() => {
  const want = %(needle)s.toLowerCase();
  const tags = %(tags)s;
  const nodes = document.querySelectorAll(tags.join(','));
  for (const el of nodes) {
    const r = el.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue;
    const label = ((el.innerText || el.textContent || '') + ' ' +
                   (el.getAttribute('aria-label') || '') + ' ' +
                   (el.getAttribute('title') || '') + ' ' +
                   (el.value || '')).toLowerCase();
    if (label.includes(want)) return el;
  }
  return null;
})()
"""

# Element kinds a user can actually act on. Kept narrow so a phrase does not
# match a giant wrapper <div> that happens to contain the text.
_CLICKABLE_TAGS = [
    "button", "a", "input[type=submit]", "input[type=button]",
    "[role=button]", "[role=link]", "[role=menuitem]", "[role=tab]",
    "[onclick]", "label",
]


def _finder_by_text(needle: str, tags: list[str] | None = None) -> str:
    return _BY_TEXT_JS % {
        "needle": json.dumps(needle),
        "tags": json.dumps(tags or _CLICKABLE_TAGS),
    }


def _box_from(result: Any) -> DomBox | None:
    if not isinstance(result, dict):
        return None
    try:
        return DomBox(
            x=int(result["x"]), y=int(result["y"]),
            width=int(result["width"]), height=int(result["height"]),
            text=str(result.get("text", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def locate_text(phrase: str, port: int = DEFAULT_PORT) -> DomBox | None:
    """Find a clickable element matching ``phrase``; screen-space box or None."""
    phrase = (phrase or "").strip()
    if not phrase:
        return None
    js = _BOX_JS % {"finder": _finder_by_text(phrase)}
    return _box_from(evaluate(js, port))


def locate_selector(selector: str, port: int = DEFAULT_PORT) -> DomBox | None:
    """Find an element by CSS selector; screen-space box or None."""
    selector = (selector or "").strip()
    if not selector:
        return None
    js = _BOX_JS % {"finder": f"document.querySelector({json.dumps(selector)})"}
    return _box_from(evaluate(js, port))


def click_text(phrase: str, port: int = DEFAULT_PORT) -> bool:
    """Click an element by its visible text, inside the page.

    A real in-page ``.click()`` — no mouse movement, so it cannot miss and is
    unaffected by window position, scroll, or z-order.
    """
    phrase = (phrase or "").strip()
    if not phrase:
        return False
    js = f"(() => {{ const el = {_finder_by_text(phrase)}; if (!el) return false; el.click(); return true; }})()"
    return evaluate(js, port) is True


def click_selector(selector: str, port: int = DEFAULT_PORT) -> bool:
    """Click the first element matching a CSS selector, inside the page."""
    selector = (selector or "").strip()
    if not selector:
        return False
    js = (
        f"(() => {{ const el = document.querySelector({json.dumps(selector)}); "
        "if (!el) return false; el.click(); return true; })()"
    )
    return evaluate(js, port) is True


def exists(selector: str, port: int = DEFAULT_PORT) -> bool:
    """Whether any visible element matches a CSS selector."""
    js = (
        f"(() => {{ const el = document.querySelector({json.dumps(selector)}); "
        "if (!el) return false; const r = el.getBoundingClientRect(); "
        "return r.width > 0 && r.height > 0; })()"
    )
    return evaluate(js, port) is True


# --- connection management ---------------------------------------------------

def _get_connection(port: int) -> "_Connection | None":
    """The live DevTools socket for the active tab, opening one if needed."""
    global _conn
    if _conn is not None and _conn.alive:
        return _conn
    tab = active_tab(port)
    if tab is None or not tab.ws_url:
        return None
    try:
        _conn = _Connection(tab.ws_url)
    except Exception:
        _conn = None
    return _conn


def _reset_connection() -> None:
    with _lock:
        _reset_connection_locked()


def _reset_connection_locked() -> None:
    """Drop the cached socket. Caller must already hold ``_lock``."""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None


def reset() -> None:
    """Forget any attached tab — called when the browser is closed/restarted."""
    _reset_connection()


class _Connection:
    """A minimal DevTools WebSocket client (RFC 6455, client role).

    Only what CDP needs: a text-frame request/response pair with an incrementing
    message id. Server frames are never masked; client frames always are.
    """

    def __init__(self, ws_url: str, timeout: float = _WS_TIMEOUT_S) -> None:
        host, port, path = _split_ws_url(ws_url)
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._handshake(host, port, path)
        self._next_id = 0
        self.alive = True

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            # Chromium >= 111 rejects DevTools sockets from unknown origins
            # unless the browser was started with --remote-allow-origins.
            # Sending no Origin header at all keeps us in the allowed case.
            "\r\n"
        )
        self._sock.sendall(request.encode())
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("DevTools closed the connection during handshake")
            header += chunk
            if len(header) > 65536:
                raise ConnectionError("DevTools handshake response was implausibly large")
        if b" 101 " not in header.split(b"\r\n", 1)[0]:
            raise ConnectionError("DevTools refused the WebSocket upgrade")
        # Anything after the header belongs to the frame stream.
        self._buffer = header.split(b"\r\n\r\n", 1)[1]

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a CDP command and return its ``result`` payload."""
        self._next_id += 1
        message_id = self._next_id
        self._send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        # CDP interleaves unsolicited events with replies; skip to ours.
        for _ in range(50):
            reply = json.loads(self._recv())
            if reply.get("id") == message_id:
                if "error" in reply:
                    raise RuntimeError(str(reply["error"]))
                return reply.get("result")
        raise TimeoutError(f"no DevTools reply for {method}")

    def _send(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])  # FIN + text opcode
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _recv(self) -> str:
        """Read one complete (possibly fragmented) text message."""
        chunks: list[bytes] = []
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == 0x8:  # close
                self.alive = False
                raise ConnectionError("DevTools closed the connection")
            if opcode == 0x9:  # ping -> pong, then keep reading
                self._pong(payload)
                continue
            if opcode == 0xA:  # pong, ignore
                continue
            chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exactly(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exactly(8))[0]
        # Server-to-client frames are unmasked; if a mask bit is set, honour it.
        mask = self._read_exactly(4) if second & 0x80 else b""
        payload = self._read_exactly(length) if length else b""
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    def _read_exactly(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(max(4096, count - len(self._buffer)))
            if not chunk:
                self.alive = False
                raise ConnectionError("DevTools stream ended")
            self._buffer += chunk
        out, self._buffer = self._buffer[:count], self._buffer[count:]
        return out

    def _pong(self, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytearray([0x8A, 0x80 | len(payload)]) + mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def close(self) -> None:
        self.alive = False
        try:
            self._sock.close()
        except OSError:
            pass


def _split_ws_url(ws_url: str) -> tuple[str, int, str]:
    """Split ``ws://host:port/path`` into its parts."""
    rest = ws_url.split("://", 1)[-1]
    netloc, _, path = rest.partition("/")
    host, _, port_text = netloc.partition(":")
    return host or "127.0.0.1", int(port_text or 80), "/" + path
