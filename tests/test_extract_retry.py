"""The retry policy, tested without touching the network.

Fireworks answered 503, 503, 200 on three probes seconds apart for the same model.
Those two failures are the provider's, not the document's, and a benchmark that
scores them as "field not verified" reports the network as if it were the extractor.
These tests pin the behaviour: 5xx is retried, 4xx is not, and a body on the last
attempt is returned rather than swallowed.
"""
from __future__ import annotations

import io
import urllib.error

import pytest

from expediente.extract import backends


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://example.invalid", code=code, msg="boom", hdrs=None, fp=io.BytesIO(b'{"error":"upstream"}')
    )


def test_retries_transient_503(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(503)
        return _Resp(b'{"ok":true}')

    monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)
    status, body = backends._post_json("https://example.invalid", {}, "k", timeout=1, attempts=5, base_delay=0)
    assert status == 200
    assert body == {"ok": True}
    assert calls["n"] == 3, "debió reintentar dos veces antes de acertar"


def test_does_not_retry_client_errors(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        raise _http_error(400)

    monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)
    status, body = backends._post_json("https://example.invalid", {}, "k", timeout=1, attempts=5, base_delay=0)
    assert status == 400
    assert calls["n"] == 1, "un 400 no es transitorio: reintentarlo es gastar dinero"
    assert "upstream" in body["error"]


def test_gives_up_after_attempts_and_reports_last_error(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        raise _http_error(503)

    monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)
    status, body = backends._post_json("https://example.invalid", {}, "k", timeout=1, attempts=3, base_delay=0)
    assert status == 503
    assert calls["n"] == 3
    assert body["error"]


def test_network_errors_are_retried_too(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("temporary failure in name resolution")
        return _Resp(b'{"ok":true}')

    monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)
    status, _ = backends._post_json("https://example.invalid", {}, "k", timeout=1, attempts=3, base_delay=0)
    assert status == 200
    assert calls["n"] == 2


def test_read_timeout_is_retried(monkeypatch):
    """``socket.timeout`` is ``TimeoutError`` since 3.10, and it is NOT a URLError.

    It escaped the old handler and propagated through ``ThreadPoolExecutor.map``,
    aborting the entire run: 800 documents lost to one slow response.
    """
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("The read operation timed out")
        return _Resp(b'{"ok":true}')

    monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)
    status, body = backends._post_json("https://example.invalid", {}, "k", timeout=1, attempts=3, base_delay=0)
    assert status == 200
    assert calls["n"] == 2


def test_unexpected_exception_returns_a_failure_instead_of_raising(monkeypatch):
    """Cualquier excepción se convierte en un fallo contable, nunca en un crash."""
    def fake_urlopen(request, timeout=None):
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)
    status, body = backends._post_json("https://example.invalid", {}, "k", timeout=1, attempts=2, base_delay=0)
    assert status == 0
    assert "RuntimeError" in body["error"]
