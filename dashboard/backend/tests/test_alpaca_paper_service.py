"""Tests for the Alpaca paper-trading strategy-execution path: the
rebalance-order math, the two-gate env-var switches, and the `decide()`
methods added to the leaderboard registry's strategies for live/paper use.

`risk_gate_orders` itself is NOT retested here -- it's imported unmodified
from `alpaca_live_service`, already covered by `test_alpaca_live_service.py`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dashboard.backend.execution import alpaca_paper_service as svc
from dashboard.backend.domain.leaderboard.strategies import _signal_engine
from dashboard.backend.domain.leaderboard.strategies._signal_engine import DailyHistory
from dashboard.backend.domain.leaderboard.strategies.equal_weight_buyhold import EqualWeightBuyHoldStrategy
from dashboard.backend.domain.leaderboard.strategies.equal_weight_index import EqualWeightIndexStrategy
from dashboard.backend.domain.leaderboard.strategies.market_index import MarketIndexStrategy
from dashboard.backend.domain.leaderboard.strategies.mean_variance import MeanVarianceStrategy
from dashboard.backend.domain.leaderboard.strategies.momentum_effect import MomentumEffectStrategy


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    """Every `decide()` test gets its own throwaway state directory --
    real strategy state under dashboard/storage/data/paper_strategy_state/
    must never be touched by the test suite."""
    monkeypatch.setattr(_signal_engine, "LIVE_STATE_DIR", tmp_path)


# ---------------------------------------------------------------------------
# compute_rebalance_orders
# ---------------------------------------------------------------------------

def test_rebalance_buys_from_cash_when_no_position_held():
    orders = svc.compute_rebalance_orders(
        target_weights={"AAPL": 1.0}, portfolio_value=1000.0, holdings={}, prices={"AAPL": 100.0}
    )
    assert len(orders) == 1
    assert orders[0] == {"symbol": "AAPL", "side": "buy", "quantity": pytest.approx(10.0)}


def test_rebalance_sells_to_reduce_an_oversized_position():
    orders = svc.compute_rebalance_orders(
        target_weights={"AAPL": 0.5}, portfolio_value=1000.0, holdings={"AAPL": 10.0}, prices={"AAPL": 100.0}
    )
    assert len(orders) == 1
    assert orders[0]["side"] == "sell"
    assert orders[0]["quantity"] == pytest.approx(5.0)


def test_rebalance_liquidates_a_holding_absent_from_target_weights():
    orders = svc.compute_rebalance_orders(
        target_weights={}, portfolio_value=1000.0, holdings={"MSFT": 3.0}, prices={"MSFT": 100.0}
    )
    assert orders == [{"symbol": "MSFT", "side": "sell", "quantity": 3.0}]


def test_rebalance_skips_a_sub_dollar_delta():
    orders = svc.compute_rebalance_orders(
        target_weights={"AAPL": 1.0}, portfolio_value=1000.0, holdings={"AAPL": 9.999},
        prices={"AAPL": 100.0},
    )
    assert orders == []


def test_rebalance_skips_a_symbol_with_no_usable_price():
    orders = svc.compute_rebalance_orders(
        target_weights={"ZZZZ": 1.0}, portfolio_value=1000.0, holdings={}, prices={}
    )
    assert orders == []


# ---------------------------------------------------------------------------
# Two-gate env vars (separate namespace from the live path)
# ---------------------------------------------------------------------------

def test_execute_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_EXECUTE", raising=False)
    assert svc.execute_enabled() is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
def test_execute_enabled_recognizes_truthy_values(monkeypatch, value):
    monkeypatch.setenv("ALPACA_PAPER_EXECUTE", value)
    assert svc.execute_enabled() is True


def test_max_order_usd_falls_back_on_junk(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_MAX_ORDER_USD", "not-a-number")
    assert svc.max_order_usd() == svc.DEFAULT_MAX_ORDER_USD


def test_max_order_usd_reads_a_valid_override(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_MAX_ORDER_USD", "500")
    assert svc.max_order_usd() == 500.0


# ---------------------------------------------------------------------------
# decide() semantics: None (skip) vs {} (flatten) vs a real target
# ---------------------------------------------------------------------------

def _synthetic_history(symbols, n_days=260, seed=1):
    idx = pd.bdate_range("2025-01-02", periods=n_days)
    rng = np.random.default_rng(seed)
    close = pd.DataFrame(
        {s: 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n_days)) for s in symbols}, index=idx
    )
    open_ = close.shift(1).fillna(close.iloc[0])
    high = close * 1.01
    low = close * 0.99
    volume = pd.DataFrame({s: rng.integers(1000, 10000, n_days) for s in symbols}, index=idx)
    return DailyHistory(close=close, open=open_, high=high, low=low, volume=volume)


def test_momentum_effect_decide_returns_a_real_target():
    history = _synthetic_history(["AAPL", "MSFT", "GOOGL"])
    strat = MomentumEffectStrategy({"symbols": ["AAPL", "MSFT", "GOOGL"]})
    weights = strat.decide(history)
    assert isinstance(weights, dict)
    assert weights  # at least one name should have positive momentum in 260 random-walk days
    assert all(w > 0 for w in weights.values())


def test_equal_weight_buyhold_only_establishes_once():
    history = _synthetic_history(["AAPL", "MSFT"])
    strat = EqualWeightBuyHoldStrategy({"symbols": ["AAPL", "MSFT"]})
    first = strat.decide(history)
    assert first == {"AAPL": 0.5, "MSFT": 0.5}
    # A second call (fresh instance, same persisted state) must be a no-op.
    second = EqualWeightBuyHoldStrategy({"symbols": ["AAPL", "MSFT"]}).decide(history)
    assert second is None


def test_equal_weight_index_rebalances_every_call():
    history = _synthetic_history(["AAPL", "MSFT"])
    strat = EqualWeightIndexStrategy({"symbols": ["AAPL", "MSFT"]})
    assert strat.decide(history) == {"AAPL": 0.5, "MSFT": 0.5}
    # Unlike buy & hold, calling again (even a fresh instance) keeps returning
    # a real target rather than None -- there is no "already established" state.
    assert EqualWeightIndexStrategy({"symbols": ["AAPL", "MSFT"]}).decide(history) == {"AAPL": 0.5, "MSFT": 0.5}


def test_mean_variance_refits_once_per_month_then_skips():
    history = _synthetic_history(["AAPL", "MSFT", "GOOGL"])
    strat = MeanVarianceStrategy({"symbols": ["AAPL", "MSFT", "GOOGL"]})
    first = strat.decide(history)
    assert isinstance(first, dict) and first
    assert abs(sum(first.values()) - 1.0) < 1e-6
    # Same month, fresh instance sharing the persisted state -> no-op.
    second = MeanVarianceStrategy({"symbols": ["AAPL", "MSFT", "GOOGL"]}).decide(history)
    assert second is None


def test_market_index_refuses_a_synthetic_index_symbol():
    history = _synthetic_history(["DIA"])
    strat = MarketIndexStrategy({"symbols": ["^DJI"]})
    assert strat.decide(history) is None


def test_market_index_buy_and_hold_for_a_real_ticker():
    history = _synthetic_history(["DIA"])
    strat = MarketIndexStrategy({"symbols": ["DIA"]})
    assert strat.decide(history) == {"DIA": 1.0}
    assert MarketIndexStrategy({"symbols": ["DIA"]}).decide(history) is None


def test_decide_with_empty_history_never_raises():
    empty = pd.DataFrame()
    history = DailyHistory(close=empty, open=empty, high=empty, low=empty, volume=empty)
    assert MomentumEffectStrategy({"symbols": ["AAPL"]}).decide(history) == {}
    assert EqualWeightBuyHoldStrategy({"symbols": ["AAPL"]}).decide(history) in ({}, None)
