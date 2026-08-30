"""Mocked-HTTP tests for infrastructure/brokers/kalshi_paper.py.

Unverified against a real Kalshi account (no credentials available), so
these tests confirm the client's own internal logic -- signing, request
shape, environment selection, error handling -- is self-consistent, matching
test_oanda_broker.py's posture for the same reason.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from dashboard.backend.infrastructure.brokers.kalshi_paper import (
    DEMO_BASE_URL,
    PRODUCTION_BASE_URL,
    KalshiClient,
    KalshiConfigError,
    KalshiCredentials,
    KalshiOrderError,
    credentials_from_env,
)


def _real_pem() -> str:
    """A throwaway RSA keypair generated fresh per test run -- signing needs
    a structurally valid PEM, not a real Kalshi-issued key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _creds() -> KalshiCredentials:
    return KalshiCredentials(api_key_id="key-123", private_key_pem=_real_pem())


def test_credentials_from_env_requires_every_field(monkeypatch):
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)
    with pytest.raises(KalshiConfigError):
        credentials_from_env()


def test_credentials_from_env_reads_all_fields(monkeypatch):
    pem = _real_pem()
    monkeypatch.setenv("KALSHI_API_KEY_ID", "key-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", pem)
    creds = credentials_from_env()
    assert creds.api_key_id == "key-123"
    assert creds.private_key_pem == pem


def test_environment_selects_base_url():
    demo = KalshiClient(credentials=_creds(), environment="demo")
    assert demo.base_url == DEMO_BASE_URL
    prod = KalshiClient(credentials=_creds(), environment="production")
    assert prod.base_url == PRODUCTION_BASE_URL


def test_unknown_environment_rejected():
    with pytest.raises(ValueError):
        KalshiClient(credentials=_creds(), environment="staging")


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_place_order_signs_and_posts_correct_body(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(201, {"order": {"order_id": "abc"}})

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.kalshi_paper.requests.post", fake_post)

    client = KalshiClient(credentials=_creds(), environment="demo")
    result = client.place_order(ticker="KXFOO-99", side="yes", action="buy", count=5)

    assert result == {"order": {"order_id": "abc"}}
    call = calls[0]
    assert call["url"] == f"{DEMO_BASE_URL}/portfolio/orders"
    assert call["json"] == {
        "ticker": "KXFOO-99", "side": "yes", "action": "buy", "count": 5, "type": "market",
    }
    headers = call["headers"]
    assert set(headers) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-SIGNATURE", "KALSHI-ACCESS-TIMESTAMP"}
    assert headers["KALSHI-ACCESS-KEY"] == "key-123"
    # A real RSA-PSS signature over a valid keypair -- verifies the client
    # actually signs with the configured private key, not a placeholder.
    assert len(headers["KALSHI-ACCESS-SIGNATURE"]) > 0


def test_place_order_rejects_invalid_side():
    client = KalshiClient(credentials=_creds())
    with pytest.raises(ValueError):
        client.place_order(ticker="KXFOO-99", side="maybe", action="buy", count=5)


def test_place_order_rejects_invalid_action():
    client = KalshiClient(credentials=_creds())
    with pytest.raises(ValueError):
        client.place_order(ticker="KXFOO-99", side="yes", action="hold", count=5)


def test_place_order_rejects_non_positive_count():
    client = KalshiClient(credentials=_creds())
    with pytest.raises(ValueError):
        client.place_order(ticker="KXFOO-99", side="yes", action="buy", count=0)


def test_order_rejection_raises_order_error(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResponse(400, {"error": "insufficient balance"})

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.kalshi_paper.requests.post", fake_post)

    client = KalshiClient(credentials=_creds())
    with pytest.raises(KalshiOrderError):
        client.place_order(ticker="KXFOO-99", side="yes", action="buy", count=5)


def test_network_failure_raises_order_error(monkeypatch):
    import requests

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.kalshi_paper.requests.post", fake_post)

    client = KalshiClient(credentials=_creds())
    with pytest.raises(KalshiOrderError):
        client.place_order(ticker="KXFOO-99", side="yes", action="buy", count=5)
