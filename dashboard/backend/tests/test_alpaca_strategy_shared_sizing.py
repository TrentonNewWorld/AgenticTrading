"""Tests for the real-money sizing helpers in execution/_alpaca_strategy_shared.py
-- capped_portfolio_value (makes a strategy's Allocated capital an actual cap
on new buying, not just a Real Trading Leaderboard display number) and
effective_target_weights (the optional fixed "$ per stock" override).
"""

from __future__ import annotations

import tempfile

import pytest

from dashboard.backend.execution._alpaca_strategy_shared import (
    capped_portfolio_value,
    effective_target_weights,
)
from dashboard.backend.domain.leaderboard import real_trading as rt


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    db_path = tempfile.mktemp(suffix=".db")
    monkeypatch.setattr(rt, "DB_PATH", db_path)
    rt._init_schema()
    yield


def test_capped_portfolio_value_uses_allocation_when_account_has_more(monkeypatch):
    rt.set_allocation("my_strategy", 500.0)
    # Account actually has $10,000 available -- the strategy must still only
    # ever size its buys against its own $500 allocation.
    value = capped_portfolio_value("my_strategy", account_cash=10_000.0, holdings={}, prices={})
    assert value == 500.0


def test_capped_portfolio_value_uses_account_when_it_has_less(monkeypatch):
    rt.set_allocation("my_strategy", 5000.0)
    # Allocated $5000, but the account (shared across strategies) only
    # actually has $200 free right now -- never suggest spending more than
    # that, regardless of what's allocated on paper.
    value = capped_portfolio_value("my_strategy", account_cash=200.0, holdings={}, prices={})
    assert value == 200.0


def test_capped_portfolio_value_includes_existing_holdings_value():
    rt.set_allocation("my_strategy", 10_000.0)
    value = capped_portfolio_value(
        "my_strategy", account_cash=100.0, holdings={"AAPL": 2.0}, prices={"AAPL": 150.0},
    )
    assert value == 400.0  # 100 cash + 2*150 holdings, well under the 10k allocation


def test_capped_portfolio_value_defaults_without_an_explicit_allocation():
    # No set_allocation call at all -- real_trading.DEFAULT_ALLOCATION applies.
    value = capped_portfolio_value("brand_new_strategy", account_cash=1_000_000.0, holdings={}, prices={})
    assert value == rt.DEFAULT_ALLOCATION


def test_effective_target_weights_passes_through_without_an_override():
    weights = {"AAPL": 0.6, "MSFT": 0.4}
    assert effective_target_weights("no_override_strategy", weights, 1000.0) == weights


def test_effective_target_weights_applies_fixed_dollar_per_stock():
    rt.set_per_stock_amount("fixed_lot_strategy", 100.0)
    weights = {"AAPL": 0.9, "MSFT": 0.1}  # the strategy's own weighting is irrelevant once fixed
    effective = effective_target_weights("fixed_lot_strategy", weights, portfolio_value=1000.0)
    # Both symbols get the same $100/$1000 = 0.10 effective weight.
    assert effective == {"AAPL": 0.1, "MSFT": 0.1}


def test_effective_target_weights_drops_zero_and_negative_weight_symbols():
    rt.set_per_stock_amount("fixed_lot_strategy", 100.0)
    weights = {"AAPL": 0.5, "MSFT": 0.0, "TSLA": -0.1}
    effective = effective_target_weights("fixed_lot_strategy", weights, portfolio_value=1000.0)
    assert effective == {"AAPL": 0.1}


def test_effective_target_weights_falls_back_when_portfolio_value_is_zero():
    rt.set_per_stock_amount("fixed_lot_strategy", 100.0)
    weights = {"AAPL": 0.5}
    assert effective_target_weights("fixed_lot_strategy", weights, portfolio_value=0.0) == weights
