"""HTTP-contract tests for /api/v1/manual10/* -- error handling and response
shape. The state-machine/upload-workflow logic itself is covered by
test_manual10_engine.py / test_manual10_uploads.py / test_manual10_sandbox.py;
these tests isolate the route layer (validation, error mapping, DB
isolation) with a fake market session so they don't depend on real market
hours or Alpaca credentials.
"""

from __future__ import annotations

import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.domain.manual10 import market_clock, repository as repo
from dashboard.backend.domain.manual10.repository import TOP_10_STRATEGY_KEY

TRADING_DATE = "2026-08-21"


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    db_path = tempfile.mktemp(suffix=".db")
    import dashboard.backend.domain.manual10.repository as repo_module
    monkeypatch.setattr(repo_module, "DB_PATH", db_path)
    repo_module._init_schema()
    yield


@pytest.fixture(autouse=True)
def _fake_session(monkeypatch):
    open_at = datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc)
    close_at = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)

    class _Session:
        trading_date = date(2026, 8, 21)
        has_session = True
        now = open_at + timedelta(hours=2)
        open_at_ = open_at
        close_at_ = close_at

    def fake_get_today_session(today=None):
        s = _Session()
        s.open_at = open_at
        s.close_at = close_at
        return s

    import dashboard.backend.api.routers.manual10 as manual10_router
    monkeypatch.setattr(manual10_router.market_clock, "get_today_session", fake_get_today_session)
    monkeypatch.setattr(market_clock, "get_today_session", fake_get_today_session)
    monkeypatch.setattr(manual10_router.market_clock, "today_trading_date", lambda: date(2026, 8, 21))
    monkeypatch.setattr(market_clock, "today_trading_date", lambda: date(2026, 8, 21))


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    import dashboard.backend.domain.manual10.uploads as uploads_module
    monkeypatch.setattr(uploads_module, "HAS_ANTHROPIC", False)


@pytest.fixture
def client():
    return TestClient(app)


VALID_CODE = """
def decide(price_history):
    return {}
"""


def test_status_returns_defaults_for_a_fresh_day(client):
    resp = client.get("/api/v1/manual10/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trading_date"] == "2026-08-21"
    assert body["open_positions_value"] == 0
    assert "scheduler_running" in body


def test_settings_round_trip(client):
    resp = client.get("/api/v1/manual10/settings")
    assert resp.status_code == 200
    assert resp.json()["top_n"] == 10

    resp = client.put("/api/v1/manual10/settings", json={"top_n": 5, "buy_in_per_stock": 25})
    assert resp.status_code == 200
    body = resp.json()
    assert body["top_n"] == 5
    assert body["buy_in_per_stock"] == 25


def test_settings_rejects_invalid_price_range(client):
    resp = client.put("/api/v1/manual10/settings", json={"price_min": 100, "price_max": 50})
    assert resp.status_code == 400


def test_settings_rejects_non_positive_values(client):
    resp = client.put("/api/v1/manual10/settings", json={"top_n": 0})
    assert resp.status_code == 400


def test_settings_rejects_empty_body(client):
    resp = client.put("/api/v1/manual10/settings", json={})
    assert resp.status_code == 400


def test_strategies_list_includes_top_10_builtin(client):
    resp = client.get("/api/v1/manual10/strategies")
    assert resp.status_code == 200
    keys = [s["key"] for s in resp.json()["strategies"]]
    assert TOP_10_STRATEGY_KEY in keys
    top10 = next(s for s in resp.json()["strategies"] if s["key"] == TOP_10_STRATEGY_KEY)
    assert top10["kind"] == "builtin"
    assert top10["selected"] is False
    assert top10["activated"] is False


def test_strategies_and_status_survive_alpaca_being_unreachable(client, monkeypatch):
    """Regression: listing/selecting strategies is a pure local-DB read and
    must not require a live Alpaca calendar call -- previously _today_str()
    routed through get_today_session() (which does), so an invalid/expired
    Alpaca key or a network blip 500'd both /strategies and /status even
    though neither actually needs live market-session data."""
    import dashboard.backend.api.routers.manual10 as manual10_router

    def _boom(today=None):
        raise RuntimeError("Alpaca calendar unreachable")

    monkeypatch.setattr(manual10_router.market_clock, "get_today_session", _boom)

    resp = client.get("/api/v1/manual10/strategies")
    assert resp.status_code == 200
    assert TOP_10_STRATEGY_KEY in [s["key"] for s in resp.json()["strategies"]]

    resp = client.get("/api/v1/manual10/status")
    assert resp.status_code == 200

    resp = client.post(f"/api/v1/manual10/strategies/{TOP_10_STRATEGY_KEY}/select")
    assert resp.status_code == 200


def test_select_and_activate_top_10(client):
    resp = client.post(f"/api/v1/manual10/strategies/{TOP_10_STRATEGY_KEY}/select")
    assert resp.status_code == 200
    assert resp.json()["selected"] == 1

    resp = client.post(f"/api/v1/manual10/strategies/{TOP_10_STRATEGY_KEY}/activate")
    assert resp.status_code == 200
    assert resp.json()["activated"] == 1

    strategies = client.get("/api/v1/manual10/strategies").json()["strategies"]
    top10 = next(s for s in strategies if s["key"] == TOP_10_STRATEGY_KEY)
    assert top10["selected"] is True
    assert top10["activated"] is True


def test_deactivate_and_deselect(client):
    client.post(f"/api/v1/manual10/strategies/{TOP_10_STRATEGY_KEY}/activate")
    resp = client.post(f"/api/v1/manual10/strategies/{TOP_10_STRATEGY_KEY}/deactivate")
    assert resp.status_code == 200
    assert resp.json()["activated"] == 0

    resp = client.post(f"/api/v1/manual10/strategies/{TOP_10_STRATEGY_KEY}/deselect")
    assert resp.status_code == 200
    assert resp.json()["selected"] == 0


def test_activate_unknown_strategy_is_404(client):
    resp = client.post("/api/v1/manual10/strategies/not_a_real_key/activate")
    assert resp.status_code == 404


def test_upload_lands_pending_and_cannot_be_activated_yet(client):
    resp = client.post("/api/v1/manual10/strategies/upload", json={
        "name": "My Strategy", "description": "desc", "code": VALID_CODE, "interval_minutes": 15,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["review_status"] == "pending"
    key = body["key"]

    resp = client.post(f"/api/v1/manual10/strategies/{key}/activate")
    assert resp.status_code == 400
    assert "not approved" in resp.json()["detail"] or "pending" in resp.json()["detail"]


def test_upload_rejects_dangerous_code(client):
    resp = client.post("/api/v1/manual10/strategies/upload", json={
        "name": "Evil", "description": "", "code": "import os\ndef decide(x):\n    return {}", "interval_minutes": 15,
    })
    assert resp.status_code == 400


def test_approve_then_activate_succeeds(client):
    created = client.post("/api/v1/manual10/strategies/upload", json={
        "name": "Good One", "description": "", "code": VALID_CODE, "interval_minutes": 15,
    }).json()
    key = created["key"]

    approved = client.post(f"/api/v1/manual10/strategies/{key}/approve")
    assert approved.status_code == 200
    assert approved.json()["review_status"] == "approved"

    activated = client.post(f"/api/v1/manual10/strategies/{key}/activate")
    assert activated.status_code == 200
    assert activated.json()["activated"] == 1


def test_delete_uploaded_strategy(client):
    created = client.post("/api/v1/manual10/strategies/upload", json={
        "name": "Delete Me", "description": "", "code": VALID_CODE, "interval_minutes": 15,
    }).json()
    resp = client.delete(f"/api/v1/manual10/strategies/{created['key']}")
    assert resp.status_code == 200
    assert client.delete(f"/api/v1/manual10/strategies/{created['key']}").status_code == 404


def test_delete_builtin_strategy_is_rejected(client):
    resp = client.delete(f"/api/v1/manual10/strategies/{TOP_10_STRATEGY_KEY}")
    assert resp.status_code == 404  # not found among *uploaded* strategies


def test_positions_rejects_bad_bucket(client):
    resp = client.get(f"/api/v1/manual10/positions?strategy_key={TOP_10_STRATEGY_KEY}&bucket=fake")
    assert resp.status_code == 400


def test_positions_returns_empty_list_for_fresh_day(client):
    resp = client.get(f"/api/v1/manual10/positions?strategy_key={TOP_10_STRATEGY_KEY}")
    assert resp.status_code == 200
    assert resp.json()["positions"] == []


def test_sell_unknown_position_is_400(client):
    resp = client.post("/api/v1/manual10/positions/999/sell")
    assert resp.status_code == 400


def test_promote_unknown_position_is_400(client):
    resp = client.post("/api/v1/manual10/positions/999/promote")
    assert resp.status_code == 400


def test_promote_without_live_execute_is_400(client):
    pid = repo.open_position(
        trading_date=TRADING_DATE, strategy_key=TOP_10_STRATEGY_KEY, symbol="AAAA", bucket="paper",
        shares=1.0, entry_price=10.0,
    )
    resp = client.post(f"/api/v1/manual10/positions/{pid}/promote")
    assert resp.status_code == 400
    assert "disabled" in resp.json()["detail"]


def test_calendar_returns_days_list(client):
    repo.ensure_day("2026-08-20", TOP_10_STRATEGY_KEY)
    resp = client.get("/api/v1/manual10/calendar")
    assert resp.status_code == 200
    dates = [d["trading_date"] for d in resp.json()["days"]]
    assert "2026-08-20" in dates


def test_screener_shape_for_a_fresh_day(client):
    resp = client.get(f"/api/v1/manual10/screener?strategy_key={TOP_10_STRATEGY_KEY}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trading_date"] == "2026-08-21"
    assert body["candidates"] == []
    assert "window_minutes" in body
