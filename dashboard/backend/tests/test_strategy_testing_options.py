"""Sub-phase 7 of the Options-dashboard plan: the Testing pipeline (submit ->
scan -> backtest -> ready) branches correctly on asset_class -- an options
upload uses domain.options.sandbox's decide_options entrypoint and
domain.options.backtester's contract-level simulation, while the existing
stocks pipeline is completely unaffected (confirmed by grep-diffing: no
change to any pre-existing stocks test in this run).
"""

from __future__ import annotations

import tempfile

import pytest

from dashboard.backend.domain.strategy_testing import repository as repo, scanner, worker

_VALID_OPTIONS_CODE = """
def decide_options(as_of, positions, chain, account):
    return []
"""

_VALID_STOCKS_CODE = """
def decide(price_history):
    return {}
"""

_INVALID_OPTIONS_CODE_OLD_ENTRYPOINT = """
def decide(price_history):
    return {}
"""


@pytest.fixture(autouse=True)
def _isolated_queue_db(monkeypatch):
    db_path = tempfile.mktemp(suffix=".db")
    monkeypatch.setattr(repo, "DB_PATH", db_path)
    repo.init_schema()
    yield


# ---------------------------------------------------------------------------
# repository.enqueue / list_all -- asset_class threading + filtering
# ---------------------------------------------------------------------------

def test_enqueue_defaults_to_stocks():
    row = repo.enqueue(name="Test", description="", code=_VALID_STOCKS_CODE)
    assert row["asset_class"] == "stocks"


def test_enqueue_accepts_options_asset_class():
    row = repo.enqueue(name="Test", description="", code=_VALID_OPTIONS_CODE, asset_class="options")
    assert row["asset_class"] == "options"


def test_list_all_filters_by_asset_class():
    repo.enqueue(name="Stock Strat", description="", code=_VALID_STOCKS_CODE, asset_class="stocks")
    repo.enqueue(name="Options Strat", description="", code=_VALID_OPTIONS_CODE, asset_class="options")

    stocks_items = repo.list_all("stocks")
    options_items = repo.list_all("options")

    assert [i["name"] for i in stocks_items] == ["Stock Strat"]
    assert [i["name"] for i in options_items] == ["Options Strat"]


# ---------------------------------------------------------------------------
# scanner.scan -- entrypoint switches on asset_class
# ---------------------------------------------------------------------------

def test_scan_stocks_accepts_decide_entrypoint():
    result = scanner.scan(_VALID_STOCKS_CODE, "stocks")
    assert result["accepted"] is True


def test_scan_options_accepts_decide_options_entrypoint():
    result = scanner.scan(_VALID_OPTIONS_CODE, "options")
    assert result["accepted"] is True


def test_scan_options_rejects_the_old_decide_entrypoint():
    """The equities decide(price_history) contract must not satisfy the
    options scanner -- the two are deliberately incompatible, per the
    decision that options strategies are full contract-level."""
    result = scanner.scan(_INVALID_OPTIONS_CODE_OLD_ENTRYPOINT, "options")
    assert result["accepted"] is False
    assert result["verdict"] == "rejected"


def test_scan_stocks_rejects_the_options_entrypoint():
    result = scanner.scan(_VALID_OPTIONS_CODE, "stocks")
    assert result["accepted"] is False
    assert result["verdict"] == "rejected"


# ---------------------------------------------------------------------------
# worker._process_one -- branches to the right backtester
# ---------------------------------------------------------------------------

def test_worker_runs_options_backtest_for_an_options_item(monkeypatch):
    row = repo.enqueue(name="Options Strat", description="", code=_VALID_OPTIONS_CODE, asset_class="options")

    called_with = {}

    def _fake_options_backtest(code):
        called_with["code"] = code
        return {"overall": {"final": 1000.0}, "months": [], "curve": [], "window": {}, "n_decisions": 0}

    def _boom_stocks_backtest(code):
        raise AssertionError("must not call the stocks backtester for an options item")

    monkeypatch.setattr(worker.backtester, "run_options_backtest", _fake_options_backtest)
    monkeypatch.setattr(worker.backtester, "run_backtest", _boom_stocks_backtest)

    processed = worker._process_one()
    assert processed is True
    assert called_with["code"] == _VALID_OPTIONS_CODE

    updated = repo.get(row["id"])
    assert updated["status"] == "ready"


def test_worker_runs_stocks_backtest_for_a_stocks_item(monkeypatch):
    row = repo.enqueue(name="Stock Strat", description="", code=_VALID_STOCKS_CODE, asset_class="stocks")

    called_with = {}

    def _fake_stocks_backtest(code):
        called_with["code"] = code
        return {"overall": {"final": 1000.0}, "months": [], "curve": [], "window": {}, "n_decisions": 0}

    def _boom_options_backtest(code):
        raise AssertionError("must not call the options backtester for a stocks item")

    monkeypatch.setattr(worker.backtester, "run_backtest", _fake_stocks_backtest)
    monkeypatch.setattr(worker.backtester, "run_options_backtest", _boom_options_backtest)

    processed = worker._process_one()
    assert processed is True
    assert called_with["code"] == _VALID_STOCKS_CODE

    updated = repo.get(row["id"])
    assert updated["status"] == "ready"


def test_worker_rejects_an_options_item_with_the_wrong_entrypoint():
    repo.enqueue(
        name="Bad Options Strat", description="", code=_INVALID_OPTIONS_CODE_OLD_ENTRYPOINT,
        asset_class="options",
    )
    processed = worker._process_one()
    assert processed is True

    items = repo.list_all("options")
    assert items[0]["status"] == "rejected"
