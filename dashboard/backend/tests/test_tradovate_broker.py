"""Mocked-HTTP tests for infrastructure/brokers/tradovate_paper.py.

This client is UNVERIFIED against a live Tradovate account (see its module
docstring -- real API access requires either a $1,000+ funded account or a
partner application, neither available to spike against). These tests can
only confirm the client's own internal logic -- request shape, header
construction, token caching -- is self-consistent; they cannot confirm the
wire format matches Tradovate's real API the way test_alpaca_options_broker.py
can for a broker that WAS live-spiked.
"""

from __future__ import annotations

import pytest

from dashboard.backend.infrastructure.brokers.tradovate_paper import (
    TradovateConfigError,
    TradovateCredentials,
    TradovateOrderError,
    TradovatePaperClient,
    credentials_from_env,
)


def _creds() -> TradovateCredentials:
    return TradovateCredentials(
        name="demo_user", password="demo_pass", cid="1234", sec="secret",
        account_spec="demo_user", account_id=987,
    )


def test_credentials_from_env_requires_every_field(monkeypatch):
    monkeypatch.delenv("TRADOVATE_USERNAME", raising=False)
    with pytest.raises(TradovateConfigError):
        credentials_from_env()


def test_credentials_from_env_reads_all_fields(monkeypatch):
    monkeypatch.setenv("TRADOVATE_USERNAME", "u")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "p")
    monkeypatch.setenv("TRADOVATE_CID", "1")
    monkeypatch.setenv("TRADOVATE_SECRET", "s")
    monkeypatch.setenv("TRADOVATE_ACCOUNT_SPEC", "u")
    monkeypatch.setenv("TRADOVATE_ACCOUNT_ID", "42")
    creds = credentials_from_env()
    assert creds.name == "u"
    assert creds.account_id == 42


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_place_order_authenticates_then_posts_order(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        if url.endswith("/auth/accesstokenrequest"):
            return _FakeResponse(200, {"accessToken": "tok123", "expirationTime": "2099-01-01T00:00:00Z"})
        if url.endswith("/order/placeorder"):
            return _FakeResponse(200, {"orderId": 555})
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.tradovate_paper.requests.post", fake_post)

    client = TradovatePaperClient(credentials=_creds())
    result = client.place_order(symbol="ES=F", side="buy", qty=1)

    assert result == {"orderId": 555}
    assert len(calls) == 2
    auth_call, order_call = calls
    assert auth_call["json"] == {
        "name": "demo_user", "password": "demo_pass",
        "appId": "NewWorldTrading", "appVersion": "1.0",
        "cid": "1234", "sec": "secret",
    }
    assert order_call["headers"] == {"Authorization": "Bearer tok123"}
    assert order_call["json"]["action"] == "Buy"
    assert order_call["json"]["symbol"] == "ES=F"
    assert order_call["json"]["orderQty"] == 1
    assert order_call["json"]["accountId"] == 987


def test_place_order_reuses_cached_token_across_calls(monkeypatch):
    auth_calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/auth/accesstokenrequest"):
            auth_calls.append(1)
            return _FakeResponse(200, {"accessToken": "tok", "expirationTime": "2099-01-01T00:00:00Z"})
        return _FakeResponse(200, {"orderId": 1})

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.tradovate_paper.requests.post", fake_post)

    client = TradovatePaperClient(credentials=_creds())
    client.place_order(symbol="ES=F", side="buy", qty=1)
    client.place_order(symbol="ES=F", side="sell", qty=1)

    assert len(auth_calls) == 1  # second order reused the cached token


def test_place_order_rejects_invalid_side():
    client = TradovatePaperClient(credentials=_creds())
    with pytest.raises(ValueError):
        client.place_order(symbol="ES=F", side="hold", qty=1)


def test_auth_failure_raises_order_error(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResponse(401, {"error": "bad credentials"})

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.tradovate_paper.requests.post", fake_post)

    client = TradovatePaperClient(credentials=_creds())
    with pytest.raises(TradovateOrderError):
        client.place_order(symbol="ES=F", side="buy", qty=1)


def test_order_rejection_raises_order_error(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/auth/accesstokenrequest"):
            return _FakeResponse(200, {"accessToken": "tok", "expirationTime": "2099-01-01T00:00:00Z"})
        return _FakeResponse(400, {"error": "insufficient margin"})

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.tradovate_paper.requests.post", fake_post)

    client = TradovatePaperClient(credentials=_creds())
    with pytest.raises(TradovateOrderError):
        client.place_order(symbol="ES=F", side="buy", qty=1)
