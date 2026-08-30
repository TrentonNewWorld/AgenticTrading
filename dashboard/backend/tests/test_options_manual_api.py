"""HTTP-contract tests for /api/v1/options/manual/* (Sub-phase 10 of the
Options-dashboard plan -- the router Sub-phase 5 didn't build). Mirrors
test_manual10_api.py's isolation pattern: options/repository.py delegates
to manual10/repository.py's own DB_PATH, so isolating that one module
isolates both.
"""

from __future__ import annotations

import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.domain.manual10 import market_clock

TRADING_DATE = "2026-08-21"

VALID_CODE = """
def decide_options(as_of, positions, chain, account):
    return []
"""

INVALID_CODE = """
def decide(price_history):
    return {}
"""


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    db_path = tempfile.mktemp(suffix=".db")
    import dashboard.backend.domain.manual10.repository as manual10_repo_module
    monkeypatch.setattr(manual10_repo_module, "DB_PATH", db_path)
    manual10_repo_module._init_schema()
    manual10_repo_module._migrate_schema()
    yield


@pytest.fixture(autouse=True)
def _fake_session(monkeypatch):
    open_at = datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc)
    close_at = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)

    class _Session:
        trading_date = date(2026, 8, 21)
        has_session = True
        now = open_at + timedelta(hours=2)

    def fake_get_today_session(today=None):
        s = _Session()
        s.open_at = open_at
        s.close_at = close_at
        return s

    import dashboard.backend.api.routers.options_manual as options_manual_router
    monkeypatch.setattr(options_manual_router.market_clock, "get_today_session", fake_get_today_session)
    monkeypatch.setattr(market_clock, "get_today_session", fake_get_today_session)


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    import dashboard.backend.domain.options.uploads as uploads_module
    monkeypatch.setattr(uploads_module, "HAS_ANTHROPIC", False)


@pytest.fixture
def client():
    return TestClient(app)


def _upload_and_approve(client, code=VALID_CODE, name="Test Options Strat"):
    resp = client.post(
        "/api/v1/options/manual/strategies/upload",
        json={"name": name, "description": "", "code": code, "interval_minutes": 15},
    )
    assert resp.status_code == 200, resp.text
    key = resp.json()["key"]
    approve = client.post(f"/api/v1/options/manual/strategies/{key}/approve")
    assert approve.status_code == 200, approve.text
    return key


def test_status_returns_zeroed_wallet_with_no_activity(client):
    resp = client.get("/api/v1/options/manual/status")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["open_positions_value"] == 0
    assert data["trading_date"] == TRADING_DATE


def test_list_strategies_empty_initially(client):
    resp = client.get("/api/v1/options/manual/strategies")
    assert resp.status_code == 200, resp.text
    assert resp.json()["strategies"] == []


def test_upload_strategy_lands_pending(client):
    resp = client.post(
        "/api/v1/options/manual/strategies/upload",
        json={"name": "My Options Strat", "description": "test", "code": VALID_CODE, "interval_minutes": 15},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["review_status"] == "pending"
    assert data["kind"] == "uploaded"
    assert data["key"].startswith("opt_")


def test_upload_rejects_the_stocks_decide_entrypoint(client):
    resp = client.post(
        "/api/v1/options/manual/strategies/upload",
        json={"name": "Bad Strat", "description": "", "code": INVALID_CODE, "interval_minutes": 15},
    )
    assert resp.status_code == 400
    assert "decide_options" in resp.json()["detail"]


def test_full_upload_approve_select_activate_flow(client):
    key = _upload_and_approve(client)

    listed = client.get("/api/v1/options/manual/strategies").json()["strategies"]
    assert any(s["key"] == key and s["review_status"] == "approved" for s in listed)

    select = client.post(f"/api/v1/options/manual/strategies/{key}/select")
    assert select.status_code == 200, select.text
    assert select.json()["selected"] == 1

    activate = client.post(f"/api/v1/options/manual/strategies/{key}/activate")
    assert activate.status_code == 200, activate.text
    assert activate.json()["activated"] == 1

    deactivate = client.post(f"/api/v1/options/manual/strategies/{key}/deactivate")
    assert deactivate.status_code == 200
    assert deactivate.json()["activated"] == 0

    deselect = client.post(f"/api/v1/options/manual/strategies/{key}/deselect")
    assert deselect.status_code == 200
    assert deselect.json()["selected"] == 0


def test_activate_before_approval_is_400(client):
    resp = client.post(
        "/api/v1/options/manual/strategies/upload",
        json={"name": "Pending Strat", "description": "", "code": VALID_CODE, "interval_minutes": 15},
    )
    key = resp.json()["key"]
    activate = client.post(f"/api/v1/options/manual/strategies/{key}/activate")
    assert activate.status_code == 400


def test_reject_upload(client):
    resp = client.post(
        "/api/v1/options/manual/strategies/upload",
        json={"name": "Reject Me", "description": "", "code": VALID_CODE, "interval_minutes": 15},
    )
    key = resp.json()["key"]
    rejected = client.post(f"/api/v1/options/manual/strategies/{key}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["review_status"] == "rejected"


def test_delete_uploaded_strategy(client):
    key = _upload_and_approve(client)
    resp = client.delete(f"/api/v1/options/manual/strategies/{key}")
    assert resp.status_code == 200, resp.text
    listed = client.get("/api/v1/options/manual/strategies").json()["strategies"]
    assert not any(s["key"] == key for s in listed)


def test_delete_unknown_strategy_is_404(client):
    resp = client.delete("/api/v1/options/manual/strategies/opt_does_not_exist")
    assert resp.status_code == 404


def test_positions_empty_initially(client):
    key = _upload_and_approve(client)
    resp = client.get(f"/api/v1/options/manual/positions?strategy_key={key}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["positions"] == []


def test_positions_invalid_bucket_is_400(client):
    resp = client.get("/api/v1/options/manual/positions?strategy_key=opt_x&bucket=bogus")
    assert resp.status_code == 400


def test_calendar_empty_initially(client):
    resp = client.get("/api/v1/options/manual/calendar")
    assert resp.status_code == 200, resp.text
    assert resp.json()["days"] == []


def test_sell_unknown_position_is_400(client):
    resp = client.post("/api/v1/options/manual/positions/99999/sell")
    assert resp.status_code == 400


def test_sell_open_position_closes_it(client, monkeypatch):
    key = _upload_and_approve(client)
    import dashboard.backend.domain.options.repository as options_repo
    import dashboard.backend.domain.options.engine as options_engine

    position_id = options_repo.open_position(
        trading_date=TRADING_DATE, strategy_key=key, symbol="AAPL260918C00180000",
        bucket="paper", shares=1, entry_price=5.0, leg_role="single",
    )
    monkeypatch.setattr(options_engine, "_current_price_for", lambda symbol, leg_role: 6.0)

    resp = client.post(f"/api/v1/options/manual/positions/{position_id}/sell")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "closed"
    assert resp.json()["exit_price"] == 6.0
    assert resp.json()["close_reason"] == "manual_sell"
