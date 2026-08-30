"""Mocked-HTTP tests for infrastructure/brokers/oanda_practice.py.

Unlike test_tradovate_broker.py, OANDA's v20 API is genuinely well-
documented public contract (see that module's docstring for the comparison)
-- but this client is still unverified against a real practice account (no
token was available to build against), so these tests can only confirm the
client's own internal logic is self-consistent.
"""

from __future__ import annotations

import pytest

from dashboard.backend.infrastructure.brokers.oanda_practice import (
    OandaConfigError,
    OandaCredentials,
    OandaOrderError,
    OandaPracticeClient,
    _to_oanda_instrument,
    credentials_from_env,
)


def _creds() -> OandaCredentials:
    return OandaCredentials(access_token="demo-token", account_id="101-001-12345678-001")


def test_credentials_from_env_requires_every_field(monkeypatch):
    monkeypatch.delenv("OANDA_ACCESS_TOKEN", raising=False)
    with pytest.raises(OandaConfigError):
        credentials_from_env()


def test_credentials_from_env_reads_all_fields(monkeypatch):
    monkeypatch.setenv("OANDA_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "101-001-12345678-001")
    creds = credentials_from_env()
    assert creds.access_token == "tok"
    assert creds.account_id == "101-001-12345678-001"


def test_to_oanda_instrument_converts_yahoo_symbol():
    assert _to_oanda_instrument("EURUSD=X") == "EUR_USD"
    assert _to_oanda_instrument("GBPUSD=X") == "GBP_USD"


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_place_order_posts_correct_body_and_headers(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(201, {"orderFillTransaction": {"id": "123"}})

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.oanda_practice.requests.post", fake_post)

    client = OandaPracticeClient(credentials=_creds())
    result = client.place_order(symbol="EURUSD=X", side="buy", qty=500)

    assert result == {"orderFillTransaction": {"id": "123"}}
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://api-fxpractice.oanda.com/v3/accounts/101-001-12345678-001/orders"
    assert call["headers"] == {"Authorization": "Bearer demo-token"}
    assert call["json"] == {
        "order": {
            "units": "500", "instrument": "EUR_USD",
            "timeInForce": "FOK", "type": "MARKET", "positionFill": "DEFAULT",
        }
    }


def test_place_order_sell_uses_negative_units(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["units"] = json["order"]["units"]
        return _FakeResponse(201, {})

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.oanda_practice.requests.post", fake_post)

    client = OandaPracticeClient(credentials=_creds())
    client.place_order(symbol="EURUSD=X", side="sell", qty=500)
    assert captured["units"] == "-500"


def test_place_order_rejects_invalid_side():
    client = OandaPracticeClient(credentials=_creds())
    with pytest.raises(ValueError):
        client.place_order(symbol="EURUSD=X", side="hold", qty=500)


def test_order_rejection_raises_order_error(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResponse(400, {"errorMessage": "Insufficient margin"})

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.oanda_practice.requests.post", fake_post)

    client = OandaPracticeClient(credentials=_creds())
    with pytest.raises(OandaOrderError):
        client.place_order(symbol="EURUSD=X", side="buy", qty=500)


def test_network_failure_raises_order_error(monkeypatch):
    import requests

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.oanda_practice.requests.post", fake_post)

    client = OandaPracticeClient(credentials=_creds())
    with pytest.raises(OandaOrderError):
        client.place_order(symbol="EURUSD=X", side="buy", qty=500)
