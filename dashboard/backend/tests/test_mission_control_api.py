"""GET /api/v1/mission-control/overview.

Regression coverage for a real bug: this route used to construct both
Alpaca clients with no user_id at all, so a signed-in user's own
Connections-saved key was silently ignored and the wallet amounts shown
were always the server's env-var account instead (caught live: a user
connected their Alpaca key and the displayed wallet amount never updated).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.api.routers import mission_control
from dashboard.backend.tests.auth_cookies_helpers import _cookie_session_token


@pytest.fixture
def client():
    return TestClient(app)


def _signup(client: TestClient) -> dict:
    resp = client.post(
        "/api/auth/signup",
        json={"email": f"mc-{uuid.uuid4().hex}@example.com", "display_name": "MC", "password": "securepass1"},
    )
    assert resp.status_code == 200, resp.text
    token = _cookie_session_token(client)
    return {"Authorization": f"Bearer {token}"}


class _FakeAlpacaClient:
    captured_user_ids: list = []

    def __init__(self, user_id=None):
        _FakeAlpacaClient.captured_user_ids.append(user_id)
        self.user_id = user_id

    def get_account(self):
        return {"cash": 1234.5, "portfolio_value": 1234.5, "buying_power": 1234.5, "equity": 1234.5}

    def get_positions(self):
        return []

    def get_positions_detailed(self):
        return []


@pytest.fixture(autouse=True)
def _reset_captured():
    _FakeAlpacaClient.captured_user_ids = []
    yield


def test_overview_passes_signed_in_users_id_to_both_clients(client, monkeypatch):
    headers = _signup(client)
    monkeypatch.setattr(mission_control, "AlpacaPaperTradingClient", _FakeAlpacaClient)
    monkeypatch.setattr(mission_control, "AlpacaLiveTradingClient", _FakeAlpacaClient)

    resp = client.get("/api/v1/mission-control/overview", headers=headers)
    assert resp.status_code == 200, resp.text

    # Both the paper and live client constructions must have received the
    # signed-in caller's real user_id -- not None (the pre-fix bug).
    assert len(_FakeAlpacaClient.captured_user_ids) == 2
    assert all(uid is not None for uid in _FakeAlpacaClient.captured_user_ids)


def test_overview_passes_none_when_signed_out(client, monkeypatch):
    monkeypatch.setattr(mission_control, "AlpacaPaperTradingClient", _FakeAlpacaClient)
    monkeypatch.setattr(mission_control, "AlpacaLiveTradingClient", _FakeAlpacaClient)

    resp = client.get("/api/v1/mission-control/overview")
    assert resp.status_code == 200, resp.text

    assert _FakeAlpacaClient.captured_user_ids == [None, None]


def test_overview_reflects_the_fake_alpaca_account_balance(client, monkeypatch):
    headers = _signup(client)
    monkeypatch.setattr(mission_control, "AlpacaPaperTradingClient", _FakeAlpacaClient)
    monkeypatch.setattr(mission_control, "AlpacaLiveTradingClient", _FakeAlpacaClient)

    resp = client.get("/api/v1/mission-control/overview", headers=headers)
    body = resp.json()
    assert body["paper"]["account"]["cash"] == 1234.5
    assert body["live"]["account"]["cash"] == 1234.5
