"""Shared HTTP connection pool (v6.2.0 performance).

Providers historically called the ``httpx.get``/``httpx.stream`` module
functions, which open a fresh TCP+TLS connection for every request. A
process-wide :class:`httpx.Client` keeps connections alive (HTTP keep-alive)
so repeat traffic — key validation, model catalogues, status probes, chat
streams — reuses the existing connection instead of re-handshaking every
time. This is purely a transport change: timeouts, headers, and payloads are
still supplied per call exactly as before, so request semantics are
unchanged.
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


def request(method: str, url: str, **kwargs):
    """One pooled request (drop-in for ``httpx.request``)."""
    return get_client().request(method, url, **kwargs)


def stream(method: str, url: str, **kwargs):
    """One pooled streaming request (drop-in for ``httpx.stream``)."""
    return get_client().stream(method, url, **kwargs)


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
