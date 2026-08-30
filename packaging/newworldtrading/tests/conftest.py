"""Shared test fixtures: a fake ``urlopen`` so SDK unit tests need no network."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import newworldtrading.legacy_client as legacy_client


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTP:
    """Records requests and returns programmed responses.

    ``responder(request) -> (status, payload)`` or returns an Exception to raise.
    """

    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    def urlopen(self, req, timeout=None):
        self.requests.append(req)
        result = self.responder(req)
        if isinstance(result, BaseException):
            raise result
        status, payload = result
        if status >= 400:
            body = io.BytesIO(json.dumps(payload).encode("utf-8"))
            raise urllib.error.HTTPError(req.full_url, status, "error", {}, body)
        return FakeResponse(payload)


@pytest.fixture
def fake_http(monkeypatch):
    def _install(responder):
        fake = FakeHTTP(responder)
        monkeypatch.setattr(legacy_client.urllib.request, "urlopen", fake.urlopen)
        return fake

    return _install
