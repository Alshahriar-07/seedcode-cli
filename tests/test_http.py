"""Regression tests for the pooled HTTP layer.

These cover the real bug: ``seedcode.core.http`` exposed only ``request`` and
``stream``, while providers called the httpx-style ``pooled_http.get`` /
``.post`` — so ``/provider`` and ``/model`` crashed with
``module 'seedcode.core.http' has no attribute 'get'``.

The tests below fail on the pre-fix module (AttributeError) and exercise the
actual provider code paths end-to-end through a stubbed transport.
"""

from __future__ import annotations

import httpx
import pytest

from seedcode.core import http
from seedcode.core.models import AppConfig
from seedcode.core.providers import PROVIDERS


class _StubResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://stub/")
            raise httpx.HTTPStatusError(
                "error", request=request, response=httpx.Response(self.status_code, request=request)
            )


class _StubClient:
    """Records every verb it is called with; returns a canned response."""

    def __init__(self, response: _StubResponse) -> None:
        self.response = response
        self.calls: list[tuple] = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self.response

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.response

    def put(self, url, **kw):
        self.calls.append(("PUT", url, kw))
        return self.response

    def patch(self, url, **kw):
        self.calls.append(("PATCH", url, kw))
        return self.response

    def delete(self, url, **kw):
        self.calls.append(("DELETE", url, kw))
        return self.response

    def head(self, url, **kw):
        self.calls.append(("HEAD", url, kw))
        return self.response

    def options(self, url, **kw):
        self.calls.append(("OPTIONS", url, kw))
        return self.response

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.response

    def stream(self, method, url, **kw):
        self.calls.append(("STREAM", method, url, kw))
        return self.response


@pytest.fixture()
def stub(monkeypatch):
    client = _StubClient(_StubResponse())
    monkeypatch.setattr(http, "get_client", lambda: client)
    return client


# --- the verbs exist and delegate --------------------------------------------

def test_http_verbs_delegate_to_the_pooled_client(stub) -> None:
    assert http.get("https://x/", timeout=1.0) is stub.response
    http.post("https://x/", json={"a": 1})
    http.put("https://x/")
    http.patch("https://x/")
    http.delete("https://x/")
    http.head("https://x/")
    http.options("https://x/")
    http.request("GET", "https://x/")
    http.stream("POST", "https://x/")

    verbs = [call[0] for call in stub.calls]
    assert verbs == [
        "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "GET", "STREAM",
    ]
    # Arguments pass through untouched (json bodies, timeouts, ...).
    assert stub.calls[1][2] == {"json": {"a": 1}}
    # stream keeps the method-first signature the providers use.
    assert stub.calls[-1][1] == "POST"


def test_get_client_is_process_wide() -> None:
    http.reset()
    try:
        assert http.get_client() is http.get_client()
    finally:
        http.reset()


# --- the real provider paths that used to crash -------------------------------

def test_openrouter_list_models_works_through_pooled_http(monkeypatch) -> None:
    payload = {
        "data": [
            {
                "id": "vendor/free-model:free",
                "name": "Free Model",
                "context_length": 4096,
                "pricing": {"prompt": "0", "completion": "0"},
            },
            {
                "id": "vendor/paid-model",
                "name": "Paid Model",
                "pricing": {"prompt": "0.5", "completion": "0.5"},
            },
        ]
    }
    client = _StubClient(_StubResponse(200, payload))
    monkeypatch.setattr(http, "get_client", lambda: client)

    models = PROVIDERS["openrouter"].list_models(AppConfig())

    assert [m.id for m in models] == ["vendor/free-model:free"]
    assert client.calls and client.calls[0][0] == "GET"


def test_openrouter_validate_key_works_through_pooled_http(monkeypatch) -> None:
    client = _StubClient(_StubResponse(200, {}))
    monkeypatch.setattr(http, "get_client", lambda: client)

    result = PROVIDERS["openrouter"].validate_key("sk-or-test")

    assert result.ok
    assert client.calls[0][0] == "GET"


def test_empty_key_still_rejected_without_a_request(monkeypatch) -> None:
    client = _StubClient(_StubResponse())
    monkeypatch.setattr(http, "get_client", lambda: client)

    result = PROVIDERS["openrouter"].validate_key("")

    assert not result.ok
    assert client.calls == []  # no network for an empty key


# --- error mapping ------------------------------------------------------------

def test_friendly_error_maps_transport_failures() -> None:
    assert "timed out" in http.friendly_error(httpx.TimeoutException("t")).lower()
    assert "connect" in http.friendly_error(httpx.ConnectError("c")).lower()

    request = httpx.Request("GET", "https://x/")
    for status, needle in ((401, "401"), (429, "429"), (503, "server error")):
        exc = httpx.HTTPStatusError(
            "e", request=request, response=httpx.Response(status, request=request)
        )
        assert needle in http.friendly_error(exc).lower()

    assert "json" in http.friendly_error(ValueError("bad json")).lower()


def test_friendly_error_never_leaks_authorization() -> None:
    request = httpx.Request(
        "GET", "https://x/", headers={"Authorization": "Bearer sk-super-secret"}
    )
    exc = httpx.HTTPStatusError(
        "e", request=request, response=httpx.Response(401, request=request)
    )
    assert "sk-super-secret" not in http.friendly_error(exc)
