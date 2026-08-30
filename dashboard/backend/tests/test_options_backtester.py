"""Sub-phase 6 of the Options-dashboard plan: the contract-level options
backtester -- the highest-risk piece, per the plan, because it has to
reconstruct history Alpaca cannot query directly (candidate contracts are
synthesized/probed, see domain/options/contracts.py). Fully stubbed: no
network access. Drives one scripted long-call strategy through
open -> hold -> expire-worthless, and a second through expire-in-the-money,
asserting equity curve values match hand-computed P&L.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from dashboard.backend.domain.options import backtester
from dashboard.backend.domain.options.contracts import CandidateContract

DAY1 = date(2026, 1, 5)
DAY2 = date(2026, 1, 6)
DAY3 = date(2026, 1, 7)  # expiration day -- the option itself has no bar here
SYMBOL = "XYZ260107C00100000"


def _bars_frame(rows):
    frame = pd.DataFrame(rows)
    frame["t"] = pd.to_datetime(frame["t"])
    return frame.set_index("t")


_BUY_ONCE_STRATEGY = """
def decide_options(as_of, positions, chain, account):
    if positions:
        return []
    contracts = chain.get("XYZ", [])
    if not contracts:
        return []
    return [{"action": "open", "symbol": contracts[0]["symbol"], "side": "buy", "qty": 1, "leg_role": "single"}]
"""


@pytest.fixture
def stub_candidates(monkeypatch):
    candidate = CandidateContract(
        symbol=SYMBOL, underlying="XYZ", expiration=DAY3, right="C", strike=100.0, bar_count=2,
    )
    monkeypatch.setattr(backtester, "find_candidate_contracts", lambda *a, **k: [candidate])
    return candidate


def test_long_call_expires_worthless(monkeypatch, stub_candidates):
    # Option trades day1 ($5.00) and day2 ($6.00), then stops (no bar day3,
    # its own expiration date) -- the underlying's own daily closes are what
    # make day3 still get visited and settled.
    bars = {SYMBOL: _bars_frame([
        {"t": "2026-01-05", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 10},
        {"t": "2026-01-06", "open": 6.0, "high": 6.0, "low": 6.0, "close": 6.0, "volume": 8},
    ])}
    monkeypatch.setattr(backtester, "get_option_bars", lambda *a, **k: bars)
    monkeypatch.setattr(
        backtester, "_fetch_underlying_daily_closes",
        lambda underlying, s, e: {DAY1: 98.0, DAY2: 99.0, DAY3: 95.0},  # closes OTM (< strike 100) on expiration
    )

    curve = backtester.run_backtest(_BUY_ONCE_STRATEGY, ["XYZ"], DAY1, DAY3, initial_capital=10000.0)

    by_date = {row["date"]: row["equity"] for row in curve}
    # Entry day: cash pays the $500 premium (100 * $5.00), but the freshly-
    # opened leg is marked to market at that SAME day's price -- same-day
    # entry has no gain/loss yet, so equity is unchanged from the start.
    assert by_date[DAY1.isoformat()] == pytest.approx(10000.0)
    # Day2: cash still 9500 (no new trade); leg now marks at $6.00 * 100.
    assert by_date[DAY2.isoformat()] == pytest.approx(9500.0 + 600.0)
    # Expiration: OTM -> worthless, no settlement cash flow -- net loss is
    # exactly the $500 premium paid.
    assert by_date[DAY3.isoformat()] == pytest.approx(9500.0)


def test_long_call_expires_in_the_money(monkeypatch, stub_candidates):
    bars = {SYMBOL: _bars_frame([
        {"t": "2026-01-05", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 10},
        {"t": "2026-01-06", "open": 7.0, "high": 7.0, "low": 7.0, "close": 7.0, "volume": 8},
    ])}
    monkeypatch.setattr(backtester, "get_option_bars", lambda *a, **k: bars)
    monkeypatch.setattr(
        backtester, "_fetch_underlying_daily_closes",
        lambda underlying, s, e: {DAY1: 98.0, DAY2: 102.0, DAY3: 110.0},  # closes ITM (> strike 100) on expiration
    )

    curve = backtester.run_backtest(_BUY_ONCE_STRATEGY, ["XYZ"], DAY1, DAY3, initial_capital=10000.0)

    by_date = {row["date"]: row["equity"] for row in curve}
    # Entry day: same-day mark-to-market at the entry price -> breakeven.
    assert by_date[DAY1.isoformat()] == pytest.approx(10000.0)
    assert by_date[DAY2.isoformat()] == pytest.approx(9500.0 + 700.0)
    # Expiration: ITM by $10 -> cash-settled intrinsic value 1 * 10 * 100 = $1000.
    # Net: -$500 premium + $1000 settlement = +$500 vs the $10000 start.
    assert by_date[DAY3.isoformat()] == pytest.approx(10500.0)


def test_short_call_expires_in_the_money_is_a_loss(monkeypatch, stub_candidates):
    """The mirror image: a short leg pays intrinsic value at expiration
    instead of receiving it -- confirms the sign is inverted correctly for
    the short side, not just copy-pasted from the long-side test."""
    sell_strategy = """
def decide_options(as_of, positions, chain, account):
    if positions:
        return []
    contracts = chain.get("XYZ", [])
    if not contracts:
        return []
    return [{"action": "open", "symbol": contracts[0]["symbol"], "side": "sell", "qty": 1, "leg_role": "single"}]
"""
    bars = {SYMBOL: _bars_frame([
        {"t": "2026-01-05", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 10},
    ])}
    monkeypatch.setattr(backtester, "get_option_bars", lambda *a, **k: bars)
    monkeypatch.setattr(
        backtester, "_fetch_underlying_daily_closes",
        lambda underlying, s, e: {DAY1: 98.0, DAY2: 102.0, DAY3: 110.0},
    )

    curve = backtester.run_backtest(sell_strategy, ["XYZ"], DAY1, DAY3, initial_capital=10000.0)
    by_date = {row["date"]: row["equity"] for row in curve}
    # Entry day: cash gains the $500 credit, but the short leg's own
    # mark-to-market is a matching -$500 liability at that same price ->
    # breakeven, same reasoning as the long side.
    assert by_date[DAY1.isoformat()] == pytest.approx(10000.0)
    # Expiration: ITM by $10 -> the short leg PAYS 1 * 10 * 100 = $1000.
    # Net: +$500 premium received - $1000 paid = -$500 vs the $10000 start.
    assert by_date[DAY3.isoformat()] == pytest.approx(9500.0)


def test_run_backtest_returns_empty_curve_when_no_candidates(monkeypatch):
    monkeypatch.setattr(backtester, "find_candidate_contracts", lambda *a, **k: [])
    curve = backtester.run_backtest(_BUY_ONCE_STRATEGY, ["XYZ"], DAY1, DAY3, initial_capital=10000.0)
    assert curve == []


def test_run_backtest_returns_empty_curve_when_bars_unavailable(monkeypatch, stub_candidates):
    monkeypatch.setattr(backtester, "get_option_bars", lambda *a, **k: {})
    curve = backtester.run_backtest(_BUY_ONCE_STRATEGY, ["XYZ"], DAY1, DAY3, initial_capital=10000.0)
    assert curve == []


# Note: the trading-day calendar coming from the underlying's own closes
# (not from option-bar presence) is already exercised by every test above --
# DAY3 (the expiration date) never has an option bar for SYMBOL, yet every
# test settles correctly on it.


def test_covered_call_mixes_a_stock_leg_and_an_option_leg(monkeypatch, stub_candidates):
    """Long 100 shares + short 1 call, in one strategy -- the shape
    Sub-phase 8's opt_covered_call_starter uses. Confirms the backtester
    correctly handles a stock leg (multiplier 1, no expiration/settlement)
    alongside an option leg (multiplier 100, settles at expiration) in the
    same run."""
    covered_call_strategy = """
def decide_options(as_of, positions, chain, account):
    if positions:
        return []
    contracts = chain.get("XYZ", [])
    if not contracts:
        return []
    return [
        {"action": "open", "symbol": "XYZ", "side": "buy", "qty": 100, "leg_role": "stock"},
        {"action": "open", "symbol": contracts[0]["symbol"], "side": "sell", "qty": 1, "leg_role": "option"},
    ]
"""
    bars = {SYMBOL: _bars_frame([
        {"t": "2026-01-05", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 10},
        {"t": "2026-01-06", "open": 6.0, "high": 6.0, "low": 6.0, "close": 6.0, "volume": 8},
    ])}
    monkeypatch.setattr(backtester, "get_option_bars", lambda *a, **k: bars)
    monkeypatch.setattr(
        backtester, "_fetch_underlying_daily_closes",
        lambda underlying, s, e: {DAY1: 98.0, DAY2: 99.0, DAY3: 95.0},  # closes OTM (< strike 100) on expiration
    )

    curve = backtester.run_backtest(covered_call_strategy, ["XYZ"], DAY1, DAY3, initial_capital=10000.0)
    by_date = {row["date"]: row["equity"] for row in curve}

    # Entry day: both legs mark to their own same-day entry price -> breakeven.
    assert by_date[DAY1.isoformat()] == pytest.approx(10000.0)
    # Expiration: call expires worthless (short leg keeps the $500 premium);
    # the stock leg never settles, it's simply marked at day3's $95 close --
    # a $300 loss on the 100 shares (bought at $98). Net: -$300 + $500 = +$200.
    assert by_date[DAY3.isoformat()] == pytest.approx(10200.0)
    # This also proves the short call leg was NOT refused by the cash cap
    # below despite the long stock leg having already spent $9800 of the
    # $10000 wallet -- see test_open_exceeding_cash_is_refused_for_buy_only's
    # docstring for why "sell" opens are deliberately exempt.


def test_open_exceeding_cash_is_refused_for_buy_only(monkeypatch, stub_candidates):
    """Same cash-sufficiency cap Futures/Forex/Crypto all have -- but here
    scoped to "buy" (debit) opens only. A "sell" open is a credit, and in a
    multi-leg strategy it can be legitimately COVERED by another leg already
    held (a covered call's short call is covered by its long stock, not by
    spare cash) -- capping "sell" the same way "buy" is capped would refuse
    the dashboard's own opt_covered_call_starter outright, which the
    previous test's unchanged assertions confirm didn't happen."""
    buy_too_much_strategy = """
def decide_options(as_of, positions, chain, account):
    if positions:
        return []
    contracts = chain.get("XYZ", [])
    if not contracts:
        return []
    return [{"action": "open", "symbol": contracts[0]["symbol"], "side": "buy", "qty": 100, "leg_role": "single"}]
"""
    bars = {SYMBOL: _bars_frame([
        {"t": "2026-01-05", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 10},
    ])}
    monkeypatch.setattr(backtester, "get_option_bars", lambda *a, **k: bars)
    monkeypatch.setattr(
        backtester, "_fetch_underlying_daily_closes",
        lambda underlying, s, e: {DAY1: 98.0, DAY2: 99.0, DAY3: 95.0},
    )

    # 100 contracts * $5 premium * 100 multiplier = $50,000 -- far more than
    # the $1,000 wallet. Without the cap this silently traded on unlimited
    # implicit leverage; with it, the open is refused and the curve stays
    # flat at the starting wallet.
    curve = backtester.run_backtest(buy_too_much_strategy, ["XYZ"], DAY1, DAY1, initial_capital=1000.0)
    assert curve[0]["equity"] == pytest.approx(1000.0)
