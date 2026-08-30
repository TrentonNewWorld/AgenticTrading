from __future__ import annotations

from datetime import date

import dashboard.backend.domain.futures.backtester as backtester_module
from dashboard.backend.domain.futures.backtester import run_backtest

# Prices are scaled to fit comfortably under the $1,000 backtest wallet
# (a real ES=F contract's ~$5,000+ notional wouldn't fit at all, and the
# backtester now correctly refuses an open that would exceed available
# cash -- see backtester.py's cash-sufficiency check).
BARS = {
    "ES=F": [
        {"t": "2026-08-01", "o": 500, "h": 501, "l": 499, "c": 500, "v": 100},
        {"t": "2026-08-02", "o": 500, "h": 506, "l": 499, "c": 505, "v": 100},
        {"t": "2026-08-03", "o": 505, "h": 511, "l": 504, "c": 510, "v": 100},
    ],
}


def _fake_daily_bars(symbol, start, end):
    return BARS.get(symbol, [])


def test_no_trades_curve_stays_flat_at_initial_capital(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_futures_daily_bars", _fake_daily_bars)
    code = """
def decide_futures(as_of, positions, quotes, account):
    return []
"""
    curve = run_backtest(code, ["ES=F"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert [p["equity"] for p in curve] == [1000.0, 1000.0, 1000.0]
    assert [p["date"] for p in curve] == ["2026-08-01", "2026-08-02", "2026-08-03"]


def test_buy_and_hold_tracks_mark_to_market(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_futures_daily_bars", _fake_daily_bars)
    code = """
def decide_futures(as_of, positions, quotes, account):
    if as_of == "2026-08-01" and not positions:
        return [{"action": "open", "symbol": "ES=F", "side": "buy", "qty": 1}]
    return []
"""
    curve = run_backtest(code, ["ES=F"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    # Entry day is breakeven (cash outflow exactly offset by same-day mark),
    # then equity tracks the position's own daily close 1:1 -- same
    # entry-day-breakeven subtlety domain/options/backtester.py's tests
    # pinned down.
    assert curve[0]["equity"] == 1000.0
    assert curve[1]["equity"] == 1000.0 + (505 - 500)
    assert curve[2]["equity"] == 1000.0 + (510 - 500)


def test_short_position_profits_when_price_falls(monkeypatch):
    def falling_bars(symbol, start, end):
        return [
            {"t": "2026-08-01", "o": 500, "h": 501, "l": 499, "c": 500, "v": 100},
            {"t": "2026-08-02", "o": 500, "h": 500, "l": 490, "c": 495, "v": 100},
        ]

    monkeypatch.setattr(backtester_module, "get_futures_daily_bars", falling_bars)
    code = """
def decide_futures(as_of, positions, quotes, account):
    if as_of == "2026-08-01" and not positions:
        return [{"action": "open", "symbol": "ES=F", "side": "sell", "qty": 1}]
    return []
"""
    curve = run_backtest(code, ["ES=F"], date(2026, 8, 1), date(2026, 8, 3), 1000.0)
    assert curve[0]["equity"] == 1000.0
    assert curve[1]["equity"] == 1000.0 + (500 - 495)


def test_open_then_close_realizes_pnl_and_flattens_position(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_futures_daily_bars", _fake_daily_bars)
    code = """
def decide_futures(as_of, positions, quotes, account):
    if as_of == "2026-08-01" and not positions:
        return [{"action": "open", "symbol": "ES=F", "side": "buy", "qty": 1}]
    if as_of == "2026-08-02" and positions:
        return [{"action": "close", "symbol": "ES=F", "side": "sell", "qty": 1}]
    return []
"""
    curve = run_backtest(code, ["ES=F"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert curve[0]["equity"] == 1000.0
    assert curve[1]["equity"] == 1000.0 + (505 - 500)
    # Closed on day 2 at 505 -- day 3's flat equity confirms the position
    # really flattened rather than silently continuing to mark to market.
    assert curve[2]["equity"] == curve[1]["equity"]


def test_strategy_receives_prev_close_from_the_prior_trading_day(monkeypatch):
    """Regression test for a real bug caught by live-verifying against real
    Yahoo data: quotes originally carried only {"price": ...}, so every
    momentum/reversion strategy comparing price vs prev_close (the only
    signal decide_futures() has, see sandbox.py's docstring) silently
    skipped every symbol on every day -- a permanently flat equity curve
    that looked like "no signal" rather than a wiring bug."""
    monkeypatch.setattr(backtester_module, "get_futures_daily_bars", _fake_daily_bars)
    # run_decide_futures executes in a subprocess sandbox, so a strategy
    # can't report a value back to the test process directly -- instead,
    # verify indirectly via a strategy that only opens once prev_close is
    # known, proving quotes actually carried a real value from day 2 onward.
    trigger_code = """
def decide_futures(as_of, positions, quotes, account):
    q = quotes.get("ES=F", {})
    if not positions and q.get("prev_close") is not None:
        return [{"action": "open", "symbol": "ES=F", "side": "buy", "qty": 1}]
    return []
"""
    curve = run_backtest(trigger_code, ["ES=F"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    # Day 1 has no prev_close yet (nothing traded before it) -- day 2 does,
    # so the position opens then and equity starts tracking the mark.
    assert curve[0]["equity"] == 1000.0
    assert curve[1]["equity"] == 1000.0
    assert curve[2]["equity"] == 1000.0 + (510 - 505)


def test_open_exceeding_cash_is_refused(monkeypatch):
    """Regression test for the same bug test_strategy_receives_prev_close_...
    was written to catch, but at the notional-vs-wallet layer: a live-
    verification run against real Yahoo data found the starter strategies
    returning 500%+ "annual returns" because nothing capped a position's
    notional (price * qty) against the $1,000 wallet -- buying 1 contract at
    a real ES=F price (~$5,000-7,700) went deeply cash-negative every time,
    which is unlimited implicit leverage, not a good strategy."""
    def expensive_bars(symbol, start, end):
        return [
            {"t": "2026-08-01", "o": 5000, "h": 5010, "l": 4990, "c": 5000, "v": 100},
            {"t": "2026-08-02", "o": 5000, "h": 5060, "l": 4990, "c": 5050, "v": 100},
        ]

    monkeypatch.setattr(backtester_module, "get_futures_daily_bars", expensive_bars)
    code = """
def decide_futures(as_of, positions, quotes, account):
    if not positions:
        return [{"action": "open", "symbol": "ES=F", "side": "buy", "qty": 1}]
    return []
"""
    curve = run_backtest(code, ["ES=F"], date(2026, 8, 1), date(2026, 8, 3), 1000.0)
    # The $5,000 open never happens, so equity stays flat at the wallet's
    # own starting value the whole window.
    assert [p["equity"] for p in curve] == [1000.0, 1000.0]


def test_missing_bars_returns_empty_curve(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_futures_daily_bars", lambda symbol, start, end: [])
    code = """
def decide_futures(as_of, positions, quotes, account):
    return []
"""
    curve = run_backtest(code, ["ES=F"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert curve == []
