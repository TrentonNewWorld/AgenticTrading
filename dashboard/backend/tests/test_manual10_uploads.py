"""Tests for the uploaded-strategy workflow: submit -> static validation ->
review -> approve/reject -> tick. The sandbox's own security properties are
covered by test_manual10_sandbox.py; these tests cover the surrounding
workflow and the paper-only rebalance tick.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

import pandas as pd
import pytest

from dashboard.backend.domain.manual10 import repository as repo, uploads


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    db_path = tempfile.mktemp(suffix=".db")
    import dashboard.backend.domain.manual10.repository as repo_module
    monkeypatch.setattr(repo_module, "DB_PATH", db_path)
    repo_module._init_schema()
    yield


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    # Keep these tests offline/deterministic -- the LLM review is exercised
    # (returns the "not performed" note), never actually called.
    monkeypatch.setattr(uploads, "HAS_ANTHROPIC", False)
    monkeypatch.delenv("ALPACA_PAPER_EXECUTE", raising=False)


VALID_CODE = """
def decide(price_history):
    weights = {}
    for symbol, points in price_history.items():
        if len(points) >= 2 and points[-1]["close"] > points[0]["close"]:
            weights[symbol] = 1.0
    return weights
"""


def test_submit_upload_starts_pending_and_never_auto_approves():
    result = uploads.submit_upload(name="My Strategy", description="desc", code=VALID_CODE, interval_minutes=15)
    assert result["review_status"] == "pending"
    assert result["kind"] == "uploaded"
    assert "not" in result["review_notes"].lower() or "unknown" in result["review_notes"].lower()


def test_submit_upload_rejects_dangerous_code():
    with pytest.raises(uploads.UploadError, match="rejected by static analysis"):
        uploads.submit_upload(
            name="Evil", description="", code="import os\ndef decide(x):\n    return {}", interval_minutes=15,
        )


def test_submit_upload_rejects_bad_interval():
    with pytest.raises(uploads.UploadError, match="interval_minutes"):
        uploads.submit_upload(name="X", description="", code=VALID_CODE, interval_minutes=1)


def test_submit_upload_rejects_blank_name():
    with pytest.raises(uploads.UploadError, match="name is required"):
        uploads.submit_upload(name="  ", description="", code=VALID_CODE, interval_minutes=15)


def test_approve_and_reject_change_status():
    created = uploads.submit_upload(name="Strat A", description="", code=VALID_CODE, interval_minutes=15)
    key = created["key"]
    approved = uploads.approve_upload(key)
    assert approved["review_status"] == "approved"

    rejected = uploads.reject_upload(key)
    assert rejected["review_status"] == "rejected"


def test_approve_unknown_key_raises():
    with pytest.raises(uploads.UploadError):
        uploads.approve_upload("not_a_real_key")


def test_duplicate_names_get_distinct_keys():
    a = uploads.submit_upload(name="Same Name", description="", code=VALID_CODE, interval_minutes=15)
    b = uploads.submit_upload(name="Same Name", description="", code=VALID_CODE, interval_minutes=15)
    assert a["key"] != b["key"]


class _FakeDailyHistory:
    def __init__(self, close_df):
        self.close = close_df


class _FakePaperClient:
    def __init__(self, prices):
        self.prices = prices
        self.orders = []

    def get_quotes(self, symbols):
        return {s: self.prices[s] for s in symbols if s in self.prices}

    def submit_market_order(self, symbol, qty, side):
        self.orders.append((symbol, qty, side))
        return {"id": "fake"}


class _FakeSession:
    now = datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)


def test_tick_uploaded_strategy_rejects_unapproved(monkeypatch):
    created = uploads.submit_upload(name="Pending One", description="", code=VALID_CODE, interval_minutes=15)
    result = uploads.tick_uploaded_strategy("2026-08-21", created["key"], created, _FakeSession())
    assert result["phase"] == "not_approved"


def test_tick_uploaded_strategy_buys_a_grower_in_paper(monkeypatch):
    created = uploads.submit_upload(name="Grower Buyer", description="", code=VALID_CODE, interval_minutes=15)
    key = created["key"]
    strategy_def = uploads.approve_upload(key)

    dates = pd.date_range("2026-08-01", periods=5, freq="D")
    close_df = pd.DataFrame({
        "AAPL": [100, 101, 102, 103, 110],  # grows -> should be bought
        "MSFT": [200, 199, 198, 197, 190],  # shrinks -> should not be bought
    }, index=dates)

    monkeypatch.setattr(uploads, "fetch_daily_history", lambda client, symbols: _FakeDailyHistory(close_df))
    monkeypatch.setattr(uploads, "DJIA_30", ["AAPL", "MSFT"])
    fake_client = _FakePaperClient({"AAPL": 110.0, "MSFT": 190.0})
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.brokers.alpaca_paper.AlpacaPaperTradingClient",
        lambda: fake_client,
    )

    result = uploads.tick_uploaded_strategy("2026-08-21", key, strategy_def, _FakeSession())
    assert result["phase"] == "holding"

    positions = repo.list_positions("2026-08-21", key, bucket="paper", status="open")
    symbols = {p["symbol"] for p in positions}
    assert "AAPL" in symbols
    assert "MSFT" not in symbols
    # ALPACA_PAPER_EXECUTE is off -- no real paper order placed, only recorded.
    assert fake_client.orders == []


def test_tick_uploaded_strategy_waits_until_its_own_interval_elapses(monkeypatch):
    created = uploads.submit_upload(name="Interval Test", description="", code=VALID_CODE, interval_minutes=30)
    key = created["key"]
    strategy_def = uploads.approve_upload(key)

    dates = pd.date_range("2026-08-01", periods=3, freq="D")
    close_df = pd.DataFrame({"AAPL": [100, 101, 102]}, index=dates)
    monkeypatch.setattr(uploads, "fetch_daily_history", lambda client, symbols: _FakeDailyHistory(close_df))
    monkeypatch.setattr(uploads, "DJIA_30", ["AAPL"])
    fake_client = _FakePaperClient({"AAPL": 102.0})
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.brokers.alpaca_paper.AlpacaPaperTradingClient",
        lambda: fake_client,
    )

    first = uploads.tick_uploaded_strategy("2026-08-21", key, strategy_def, _FakeSession())
    assert first["phase"] == "holding"

    # Immediately again, same "now" -- should not re-run within the interval.
    second = uploads.tick_uploaded_strategy("2026-08-21", key, strategy_def, _FakeSession())
    assert second["phase"] == "waiting"
