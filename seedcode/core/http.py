"""Shared HTTP transport — one pooled client for the whole process.

Providers historically called the ``httpx.get``/``httpx.stream`` module
functions, which open a fresh TCP+TLS connection for every request. A
process-wide :class:`httpx.Client` keeps connections alive (HTTP keep-alive)
so repeat traffic — key validation, model catalogues, status probes, chat
streams — reuses the existing connection instead of re-handshaking every time.

To be a genuine drop-in, this module mirrors the parts of the ``httpx`` module
the providers use:

* ``get`` / ``post`` / ``put`` / ``patch`` / ``delete`` / ``head`` / ``options``
  — convenience verbs (``httpx.get`` style);
* ``request(method, url, …)`` and ``stream(method, url, …)`` — the generic
  forms, including the streaming context manager;
* ``get_client`` / ``reset`` — the pooled-client lifecycle.

Every verb funnels through the single shared client, so there is exactly one
HTTP layer (providers never construct their own). Timeouts, headers, and
payloads are still supplied per call, so request semantics are unchanged.

Secret safety: nothing here logs or echoes request headers, so an
``Authorization`` header can never reach a log line or an error message.
"""

from __future__ import annotations

import threading

import httpx

# Fallback timeout only — every call site passes its own explicit timeouts.
_DEFAULT_TIMEOUT = httpx.Timeout(20.0, read=180.0)

_lock = threading.Lock()
_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    """The process-wide pooled client (created on first use, thread-safe)."""
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                timeout=_DEFAULT_TIMEOUT,
                # One CLI user talks to a handful of hosts; a modest pool keeps
                # sockets (and TLS handshakes) bounded.
                limits=httpx.Limits(
                    max_connections=8, max_keepalive_connections=4
                ),
                headers={"User-Agent": "SeedCode/6.2"},
            )
    return _client


# --- generic forms -----------------------------------------------------------
def request(method: str, url: str, **kwargs):
    """One pooled request (drop-in for ``httpx.request``)."""
    return get_client().request(method, url, **kwargs)


def stream(method: str, url: str, **kwargs):
    """One pooled streaming request (drop-in for ``httpx.stream``)."""
    return get_client().stream(method, url, **kwargs)


# --- httpx-style convenience verbs -------------------------------------------
# Providers call these exactly like the httpx module functions; the previous
# omission of get/post was a real runtime bug ("module has no attribute 'get').
def get(url: str, **kwargs):
    """Pooled ``GET`` (drop-in for ``httpx.get``)."""
    return get_client().get(url, **kwargs)


def post(url: str, **kwargs):
    """Pooled ``POST`` (drop-in for ``httpx.post``)."""
    return get_client().post(url, **kwargs)


def put(url: str, **kwargs):
    """Pooled ``PUT`` (drop-in for ``httpx.put``)."""
    return get_client().put(url, **kwargs)


def patch(url: str, **kwargs):
    """Pooled ``PATCH`` (drop-in for ``httpx.patch``)."""
    return get_client().patch(url, **kwargs)


def delete(url: str, **kwargs):
    """Pooled ``DELETE`` (drop-in for ``httpx.delete``)."""
    return get_client().delete(url, **kwargs)


def head(url: str, **kwargs):
    """Pooled ``HEAD`` (drop-in for ``httpx.head``)."""
    return get_client().head(url, **kwargs)


def options(url: str, **kwargs):
    """Pooled ``OPTIONS`` (drop-in for ``httpx.options``)."""
    return get_client().options(url, **kwargs)


def reset() -> None:
    """Close the pooled client (session teardown / tests)."""
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = None


# --- error mapping -----------------------------------------------------------
_STATUS_HINTS = {
    400: "The provider rejected the request (HTTP 400). Check the configured model.",
    401: "Authentication failed (HTTP 401). Check the configured API key with /config.",
    403: "The provider refused the request (HTTP 403). Your key may lack access to this model.",
    404: "The requested endpoint or model was not found (HTTP 404). Pick another with /model.",
    429: "Rate limited by the provider (HTTP 429). Wait a moment and try again.",
}


def friendly_error(exc: BaseException) -> str:
    """A safe, actionable message for a transport failure.

    Differentiates timeouts, connection/DNS failures, HTTP status errors, and
    other transport errors — and never includes request headers, so an API key
    can never leak through an error string.
    """
    if isinstance(exc, httpx.TimeoutException):
        return "The request timed out. Check your connection and try again."
    if isinstance(exc, httpx.ConnectError):
        return (
            "Could not connect (network or DNS failure). "
            "Check your internet connection and provider configuration."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _STATUS_HINTS:
            return _STATUS_HINTS[status]
        if status >= 500:
            return (
                f"The provider had a server error (HTTP {status}). "
                "This is usually temporary — try again shortly."
            )
        return f"The provider returned HTTP {status}."
    if isinstance(exc, httpx.HTTPError):
        return "A network error occurred while contacting the provider."
    if isinstance(exc, ValueError):
        return "The provider returned an unexpected response (invalid JSON)."
    return "An unexpected network error occurred."


__all__ = [
    "delete",
    "friendly_error",
    "get",
    "get_client",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "request",
    "reset",
    "stream",
]
