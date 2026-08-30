"""HTTP-contract tests for /api/v1/prediction/*. Mirrors
test_crypto_manual_api.py's isolation pattern.
"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app

VALID_CODE = """
def decide_prediction(as_of, positions, markets, account):
    return []
"""

INVALID_CODE = """
def decide(price_history):
    return {}
"""


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    db_path = tempfile.mktemp(suffix=".db")
    import dashboard.backend.domain.prediction.repository as prediction_repo_module
    monkeypatch.setattr(prediction_repo_module, "DB_PATH", db_path)
    prediction_repo_module.init_schema()
    yield


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    import dashboard.backend.infrastructure.llm.backtest_harness as harness_module
    monkeypatch.setattr(harness_module, "HAS_ANTHROPIC", False)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_notice_mentions_five_days(client):
    resp = client.get("/api/v1/prediction/notice")
    assert resp.status_code == 200
    data = resp.json()
    assert data["days_required"] == 5
    assert "5 real days" in data["notice"]


def test_submit_manual_lands_in_waiting(client):
    resp = client.post("/api/v1/prediction/strategies/manual", json={"name": "Test", "description": "d", "code": VALID_CODE})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "waiting"
    assert data["day_count"] == 0
    assert data["source_type"] == "manual"
    assert "code" not in data


def test_submit_invalid_code_is_rejected(client):
    resp = client.post("/api/v1/prediction/strategies/upload", json={"name": "Bad", "description": "", "code": INVALID_CODE})
    assert resp.status_code == 400
    # It's still recorded, as 'rejected', not silently dropped.
    listing = client.get("/api/v1/prediction/strategies").json()["strategies"]
    assert any(s["name"] == "Bad" and s["status"] == "rejected" for s in listing)


def test_upload_gets_a_review_notes_field_manual_does_not(client):
    manual = client.post("/api/v1/prediction/strategies/manual", json={"name": "M", "description": "", "code": VALID_CODE}).json()
    upload = client.post("/api/v1/prediction/strategies/upload", json={"name": "U", "description": "", "code": VALID_CODE}).json()
    assert manual["review_notes"] is None
    assert upload["review_notes"] is not None  # "No LLM available..." with HAS_ANTHROPIC patched off


def test_get_strategy_detail(client):
    created = client.post("/api/v1/prediction/strategies/manual", json={"name": "Detail", "description": "", "code": VALID_CODE}).json()
    resp = client.get(f"/api/v1/prediction/strategies/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_unknown_strategy_404s(client):
    resp = client.get("/api/v1/prediction/strategies/nonexistent")
    assert resp.status_code == 404


def test_add_before_ready_is_refused(client):
    created = client.post("/api/v1/prediction/strategies/manual", json={"name": "Early", "description": "", "code": VALID_CODE}).json()
    resp = client.post(f"/api/v1/prediction/strategies/{created['id']}/add")
    assert resp.status_code == 400
    assert "day 0 of 5" in resp.json()["detail"]


def test_add_once_ready_succeeds(client):
    import dashboard.backend.domain.prediction.repository as repo
    created = client.post("/api/v1/prediction/strategies/manual", json={"name": "Ready", "description": "", "code": VALID_CODE}).json()
    for d in ["2026-01-0" + str(n) for n in range(1, 6)]:
        repo.record_tick(
            created["id"], as_of=d, cash=1000.0, positions=[],
            equity_point={"date": d, "equity": 1000.0}, fees_paid_today=0.0,
        )
    resp = client.post(f"/api/v1/prediction/strategies/{created['id']}/add")
    assert resp.status_code == 200
    assert resp.json()["status"] == "added"


def test_delete_a_rejected_strategy_removes_it_permanently(client):
    resp = client.post("/api/v1/prediction/strategies/upload", json={"name": "Bad", "description": "", "code": INVALID_CODE})
    strategy_id = resp.json() if resp.status_code == 200 else None
    listing = client.get("/api/v1/prediction/strategies").json()["strategies"]
    rejected = next(s for s in listing if s["name"] == "Bad")
    del_resp = client.post(f"/api/v1/prediction/strategies/{rejected['id']}/delete")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"
    assert client.get(f"/api/v1/prediction/strategies/{rejected['id']}").status_code == 404


def test_delete_a_waiting_strategy_marks_deleted_not_removed(client):
    created = client.post("/api/v1/prediction/strategies/manual", json={"name": "Mid", "description": "", "code": VALID_CODE}).json()
    resp = client.post(f"/api/v1/prediction/strategies/{created['id']}/delete")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    # Still fetchable (soft-deleted, shows in History) -- unlike a rejected/error row.
    assert client.get(f"/api/v1/prediction/strategies/{created['id']}").status_code == 200
