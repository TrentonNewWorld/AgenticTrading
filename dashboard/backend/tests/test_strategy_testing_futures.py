"""The Testing pipeline (submit -> scan -> backtest -> ready) branches
correctly for asset_class="futures", mirroring
test_strategy_testing_options.py exactly.
"""

from __future__ import annotations

import tempfile

import pytest

from dashboard.backend.domain.strategy_testing import repository as repo, scanner, worker

_VALID_FUTURES_CODE = """
def decide_futures(as_of, positions, quotes, account):
    return []
"""

_VALID_STOCKS_CODE = """
def decide(price_history):
    return {}
"""


@pytest.fixture(autouse=True)
def _isolated_queue_db(monkeypatch):
    db_path = tempfile.mktemp(suffix=".db")
    monkeypatch.setattr(repo, "DB_PATH", db_path)
    repo.init_schema()
    yield


def test_enqueue_accepts_futures_asset_class():
    row = repo.enqueue(name="Test", description="", code=_VALID_FUTURES_CODE, asset_class="futures")
    assert row["asset_class"] == "futures"


def test_list_all_filters_by_asset_class():
    repo.enqueue(name="Stock Strat", description="", code=_VALID_STOCKS_CODE, asset_class="stocks")
    repo.enqueue(name="Futures Strat", description="", code=_VALID_FUTURES_CODE, asset_class="futures")

    stocks_items = repo.list_all("stocks")
    futures_items = repo.list_all("futures")

    assert [i["name"] for i in stocks_items] == ["Stock Strat"]
    assert [i["name"] for i in futures_items] == ["Futures Strat"]


def test_scan_futures_accepts_decide_futures_entrypoint():
    result = scanner.scan(_VALID_FUTURES_CODE, "futures")
    assert result["accepted"] is True


def test_scan_futures_rejects_the_stocks_entrypoint():
    result = scanner.scan(_VALID_STOCKS_CODE, "futures")
    assert result["accepted"] is False
    assert result["verdict"] == "rejected"


def test_scan_stocks_rejects_the_futures_entrypoint():
    result = scanner.scan(_VALID_FUTURES_CODE, "stocks")
    assert result["accepted"] is False
    assert result["verdict"] == "rejected"


def test_worker_runs_futures_backtest_for_a_futures_item(monkeypatch):
    row = repo.enqueue(name="Futures Strat", description="", code=_VALID_FUTURES_CODE, asset_class="futures")

    called_with = {}

    def _fake_futures_backtest(code):
        called_with["code"] = code
        return {"overall": {"final": 1000.0}, "months": [], "curve": [], "window": {}, "n_decisions": 0}

    def _boom_stocks_backtest(code):
        raise AssertionError("must not call the stocks backtester for a futures item")

    monkeypatch.setattr(worker.backtester, "run_futures_backtest", _fake_futures_backtest)
    monkeypatch.setattr(worker.backtester, "run_backtest", _boom_stocks_backtest)

    processed = worker._process_one()
    assert processed is True
    assert called_with["code"] == _VALID_FUTURES_CODE

    updated = repo.get(row["id"])
    assert updated["status"] == "ready"


def test_worker_rejects_a_futures_item_with_the_wrong_entrypoint():
    repo.enqueue(name="Bad Futures Strat", description="", code=_VALID_STOCKS_CODE, asset_class="futures")
    processed = worker._process_one()
    assert processed is True

    items = repo.list_all("futures")
    assert items[0]["status"] == "rejected"
