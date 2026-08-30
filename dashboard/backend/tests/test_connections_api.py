"""API Connections page: save/list/remove provider credentials + paper-wallet mode."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import dashboard.backend.domain.brokers.repository as brokers_repo
import dashboard.backend.domain.connections.repository as connections_repo
import dashboard.backend.users as users_module
from dashboard.backend.app import app
from dashboard.backend.tests.auth_cookies_helpers import _cookie_session_token


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(brokers_repo._KEY_ENV_VAR, Fernet.generate_key().decode())
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        monkeypatch.setattr(users_module, "user_store", users_module.UserStore(db_path=root / "users.db"))
        monkeypatch.setattr(
            connections_repo, "connection_store", connections_repo.ConnectionStore(db_path=root / "content.db")
        )
        monkeypatch.setattr(
            "dashboard.backend.api.routers.connections.connection_store",
            connections_repo.connection_store,
        )
        yield TestClient(app)


def _signup(client: TestClient, email: str = "conn@example.com") -> dict:
    resp = client.post(
        "/api/auth/signup",
        json={"email": email, "display_name": "Conn", "password": "securepass1"},
    )
    assert resp.status_code == 200, resp.text
    token = _cookie_session_token(client)
    return {
        "Authorization": f"Bearer {token}",
        "X-Session-Id": str(uuid.uuid4()),
    }


def test_list_connections_defaults_all_disconnected(client):
    headers = _signup(client)
    resp = client.get("/api/v1/connections", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data["connections"].keys()) == {
        "alpaca_live", "alpaca_ira", "alpaca_paper", "anthropic_subscription",
        "anthropic_api", "openrouter", "commonstack", "local",
        "kalshi", "polymarket", "tradovate", "oanda",
    }
    for info in data["connections"].values():
        assert info["connected"] is False
    assert data["paper_wallet"] == {"mode": "virtual", "virtual_balance": 10000.0}


def test_save_and_delete_alpaca_paper_connection(client):
    headers = _signup(client)
    saved = client.put(
        "/api/v1/connections/alpaca_paper",
        headers=headers,
        json={"api_key": "PKTESTKEY123", "secret_key": "shh"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["connected"] is True
    assert saved.json()["hint"].endswith("123")

    listed = client.get("/api/v1/connections", headers=headers).json()
    assert listed["connections"]["alpaca_paper"]["connected"] is True
    # Other providers untouched.
    assert listed["connections"]["alpaca_live"]["connected"] is False

    deleted = client.delete("/api/v1/connections/alpaca_paper", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["connected"] is False

    listed_after = client.get("/api/v1/connections", headers=headers).json()
    assert listed_after["connections"]["alpaca_paper"]["connected"] is False


def test_save_and_delete_alpaca_ira_connection(client):
    """Regression: a distinct Alpaca account from alpaca_live/alpaca_paper --
    its own provider entry, its own credentials, must not be conflated with
    either (a saved IRA key overwriting the live key would be exactly the
    cross-account mixup this provider exists to prevent)."""
    headers = _signup(client)
    saved = client.put(
        "/api/v1/connections/alpaca_ira",
        headers=headers,
        json={"api_key": "PKIRAKEY456", "secret_key": "irasecret"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["connected"] is True
    assert saved.json()["hint"].endswith("456")

    listed = client.get("/api/v1/connections", headers=headers).json()
    assert listed["connections"]["alpaca_ira"]["connected"] is True
    assert listed["connections"]["alpaca_live"]["connected"] is False
    assert listed["connections"]["alpaca_paper"]["connected"] is False

    deleted = client.delete("/api/v1/connections/alpaca_ira", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["connected"] is False


def test_save_missing_required_field_is_400(client):
    headers = _signup(client)
    resp = client.put(
        "/api/v1/connections/alpaca_live",
        headers=headers,
        json={"api_key": "onlykey"},
    )
    assert resp.status_code == 400
    assert "secret_key" in resp.json()["detail"]


def test_unknown_provider_is_404(client):
    headers = _signup(client)
    resp = client.put(
        "/api/v1/connections/coinbase",
        headers=headers,
        json={"api_key": "x"},
    )
    assert resp.status_code == 404


def test_single_field_providers(client):
    headers = _signup(client)
    for provider, body in [
        ("anthropic_subscription", {"token": "sess-tok"}),
        ("anthropic_api", {"api_key": "sk-ant-x"}),
        ("openrouter", {"api_key": "or-key"}),
        ("commonstack", {"api_key": "cs-key"}),
        ("local", {"url": "http://localhost:11434"}),
    ]:
        resp = client.put(f"/api/v1/connections/{provider}", headers=headers, json=body)
        assert resp.status_code == 200, (provider, resp.text)
        assert resp.json()["connected"] is True


def test_save_oanda_connection(client):
    """Regression: ConnectionBody (the request schema) originally only
    declared api_key/secret_key/token/url -- Kalshi and Polymarket happened
    to reuse those names, but OANDA's access_token/account_id fields weren't
    declared at all, so Pydantic silently dropped them before
    connection_store ever saw them, and every save 400'd with "access_token
    is required" even though the client sent it correctly."""
    headers = _signup(client)
    resp = client.put(
        "/api/v1/connections/oanda",
        headers=headers,
        json={"access_token": "practice-tok-123", "account_id": "001-002-1234567-001"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["connected"] is True


def test_save_tradovate_connection(client):
    headers = _signup(client)
    resp = client.put(
        "/api/v1/connections/tradovate",
        headers=headers,
        json={
            "username": "trader1", "password": "pw", "cid": "1234", "sec": "s3cr3t",
            "account_spec": "trader1", "account_id": "555",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["connected"] is True


def test_paper_wallet_mode_and_virtual_balance(client):
    headers = _signup(client)
    resp = client.put(
        "/api/v1/connections/paper-wallet",
        headers=headers,
        json={"mode": "alpaca"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "alpaca"

    resp2 = client.put(
        "/api/v1/connections/paper-wallet",
        headers=headers,
        json={"virtual_balance": 25000},
    )
    assert resp2.status_code == 200
    assert resp2.json()["virtual_balance"] == 25000.0
    # Mode from the previous call persists independently.
    assert resp2.json()["mode"] == "alpaca"

    resp3 = client.put("/api/v1/connections/paper-wallet", headers=headers, json={"mode": "bogus"})
    assert resp3.status_code == 400


def test_paper_wallet_route_not_shadowed_by_provider_route(client):
    """Regression: '/{provider}' is registered after '/paper-wallet' so a PUT
    to the literal path must reach put_paper_wallet, not save_connection with
    provider='paper-wallet' (which would 404 as an unknown provider)."""
    headers = _signup(client)
    resp = client.put(
        "/api/v1/connections/paper-wallet",
        headers=headers,
        json={"mode": "virtual"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"mode": "virtual", "virtual_balance": 10000.0}


def test_connections_require_login(client):
    resp = client.get("/api/v1/connections")
    assert resp.status_code == 401


def test_commonstack_connect_status_and_remove(client):
    """The gateway that fronts all 7 Competition Leaderboard AI models -- the
    Connections-page half of infrastructure/llm/providers/commonstack.py's
    existing COMMONSTACK_API_KEY env-var read."""
    headers = _signup(client)
    listed_before = client.get("/api/v1/connections", headers=headers).json()
    assert listed_before["connections"]["commonstack"]["connected"] is False

    saved = client.put(
        "/api/v1/connections/commonstack",
        headers=headers,
        json={"api_key": "cs-testkey-9999"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["connected"] is True
    assert saved.json()["hint"].endswith("9999")

    listed_after = client.get("/api/v1/connections", headers=headers).json()
    assert listed_after["connections"]["commonstack"]["connected"] is True

    deleted = client.delete("/api/v1/connections/commonstack", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["connected"] is False


def test_providers_route_is_public_and_not_shadowed(client):
    """No login required (field shape only, never a credential value), and
    registered ahead of '/{provider}' the same way '/paper-wallet' is -- a
    GET here must not be swallowed by save_connection with provider='providers'."""
    resp = client.get("/api/v1/connections/providers")
    assert resp.status_code == 200, resp.text
    providers = resp.json()["providers"]
    assert providers["commonstack"] == {"required": ["api_key"], "optional": []}
    assert providers["alpaca_paper"] == {"required": ["api_key", "secret_key"], "optional": []}
    # Field shape only -- no secret values anywhere in the payload.
    assert "credentials_enc" not in str(providers)
