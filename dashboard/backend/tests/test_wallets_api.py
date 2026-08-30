"""domain/wallets.py + api/routers/wallets.py -- per-dashboard wallet cards
and the Home page's deduped combined portfolio total.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.domain import wallets as wallets_module
from dashboard.backend.tests.auth_cookies_helpers import _cookie_session_token


@pytest.fixture
def client():
    return TestClient(app)


def _signup(client: TestClient) -> dict:
    resp = client.post(
        "/api/auth/signup",
        json={"email": f"wallets-{uuid.uuid4().hex}@example.com", "display_name": "W", "password": "securepass1"},
    )
    assert resp.status_code == 200, resp.text
    token = _cookie_session_token(client)
    return {"Authorization": f"Bearer {token}"}


class _FakeAlpaca:
    def __init__(self, user_id=None):
        self.user_id = user_id

    def get_account(self):
        return {"portfolio_value": 50000.0, "cash": 40000.0, "equity": 50000.0, "buying_power": 100000.0}


def _patch_all_alpaca(monkeypatch):
    monkeypatch.setattr(wallets_module, "_alpaca_wallet", lambda **kwargs: wallets_module.WalletSnapshot(
        broker=kwargs["provider"], label=kwargs["label"], connected=True, balance=50000.0,
        balance_label="Portfolio value", dedup_key=kwargs["provider"], purpose=kwargs["purpose"],
    ))


# ---------------------------------------------------------------------------
# domain/wallets.py -- dedup logic (the whole point of this module)
# ---------------------------------------------------------------------------

def test_portfolio_summary_counts_shared_alpaca_paper_account_once(monkeypatch):
    _patch_all_alpaca(monkeypatch)
    monkeypatch.setattr(wallets_module, "_tradovate_wallet", lambda uid: wallets_module._not_connected(
        "tradovate", "Tradovate", "tradovate", "Futures demo-exchange fills"))
    monkeypatch.setattr(wallets_module, "_oanda_wallet", lambda uid: wallets_module._not_connected(
        "oanda", "OANDA", "oanda", "Forex demo-exchange fills"))
    monkeypatch.setattr(wallets_module, "_kalshi_wallet", lambda uid: wallets_module._not_connected(
        "kalshi", "Kalshi", f"kalshi:{uid}", "Prediction demo-exchange fills"))
    monkeypatch.setattr(wallets_module, "_polymarket_wallet", lambda uid: wallets_module._not_connected(
        "polymarket", "Polymarket", f"polymarket:{uid}", "Prediction real-money trading"))

    summary = wallets_module.get_portfolio_summary(user_id=1)

    alpaca_paper_rows = [e for e in summary["entries"] if e["broker"] == "alpaca_paper"]
    assert len(alpaca_paper_rows) == 1, "Stocks+Options+Crypto sharing one Alpaca account must produce ONE row"
    assert set(alpaca_paper_rows[0]["asset_classes"]) == {"Stocks", "Options", "Crypto"}
    # Total = alpaca_paper (50000, once) + alpaca_live (50000) + alpaca_ira (50000)
    # -- not 3x alpaca_paper, and the IRA is never merged into either.
    assert summary["total"] == pytest.approx(150000.0)


def test_portfolio_summary_total_is_none_when_nothing_connected(monkeypatch):
    for name in ("_alpaca_wallet",):
        pass
    monkeypatch.setattr(
        wallets_module, "_alpaca_wallet",
        lambda **kwargs: wallets_module._not_connected(kwargs["provider"], kwargs["label"], kwargs["provider"], kwargs["purpose"]),
    )
    monkeypatch.setattr(wallets_module, "_tradovate_wallet", lambda uid: wallets_module._not_connected(
        "tradovate", "Tradovate", "tradovate", "x"))
    monkeypatch.setattr(wallets_module, "_oanda_wallet", lambda uid: wallets_module._not_connected(
        "oanda", "OANDA", "oanda", "x"))
    monkeypatch.setattr(wallets_module, "_kalshi_wallet", lambda uid: wallets_module._not_connected(
        "kalshi", "Kalshi", f"kalshi:{uid}", "x"))
    monkeypatch.setattr(wallets_module, "_polymarket_wallet", lambda uid: wallets_module._not_connected(
        "polymarket", "Polymarket", f"polymarket:{uid}", "x"))

    summary = wallets_module.get_portfolio_summary(user_id=1)
    assert summary["total"] is None


def test_dashboard_wallets_labels_shared_account(monkeypatch):
    _patch_all_alpaca(monkeypatch)
    options_wallets = wallets_module.get_dashboard_wallets("options", user_id=1)
    assert len(options_wallets) == 1
    assert set(options_wallets[0]["shared_with"]) == {"Stocks", "Crypto"}


def test_dashboard_wallets_stocks_has_three_independent_slots(monkeypatch):
    _patch_all_alpaca(monkeypatch)
    stocks_wallets = wallets_module.get_dashboard_wallets("stocks", user_id=1)
    brokers = {w["broker"] for w in stocks_wallets}
    assert brokers == {"alpaca_paper", "alpaca_live", "alpaca_ira"}
    paper = next(w for w in stocks_wallets if w["broker"] == "alpaca_paper")
    live = next(w for w in stocks_wallets if w["broker"] == "alpaca_live")
    ira = next(w for w in stocks_wallets if w["broker"] == "alpaca_ira")
    assert set(paper["shared_with"]) == {"Options", "Crypto"}
    assert live["shared_with"] == []  # live is Stocks-only, nothing to share with
    assert ira["shared_with"] == []  # a distinct account from live/paper, nothing to share with either


def test_ira_is_a_distinct_account_from_live_and_paper(monkeypatch):
    """Regression: an IRA is a genuinely separate Alpaca account (its own
    account number, its own key pair) -- it must never be deduped/merged
    with alpaca_live or alpaca_paper just because they're all "Alpaca"."""
    _patch_all_alpaca(monkeypatch)
    summary = wallets_module.get_portfolio_summary(user_id=1)
    ira_rows = [e for e in summary["entries"] if e["broker"] == "alpaca_ira"]
    assert len(ira_rows) == 1
    assert ira_rows[0]["asset_classes"] == ["Stocks"]
    # Three independent $50k accounts (paper, live, ira per _patch_all_alpaca) sum to 150k.
    assert summary["total"] == pytest.approx(150000.0)


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

def test_dashboard_wallets_route_rejects_unknown_asset_class(client):
    resp = client.get("/api/v1/wallets/not_a_real_asset_class")
    assert resp.status_code == 404


def test_dashboard_wallets_route_returns_200_for_every_asset_class(client):
    for asset_class in ("stocks", "options", "futures", "forex", "crypto", "prediction"):
        resp = client.get(f"/api/v1/wallets/{asset_class}")
        assert resp.status_code == 200, (asset_class, resp.text)
        assert resp.json()["asset_class"] == asset_class


def test_portfolio_summary_route_returns_200(client):
    resp = client.get("/api/v1/wallets/portfolio-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "entries" in body
