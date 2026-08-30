from __future__ import annotations

from datetime import date

import pytest

import dashboard.backend.domain.forex.backtester as backtester_module
from dashboard.backend.domain.forex.backtester import run_backtest

BARS = {
    "EURUSD=X": [
        {"t": "2026-08-01", "o": 1.080, "h": 1.081, "l": 1.079, "c": 1.080, "v": 0},
        {"t": "2026-08-02", "o": 1.080, "h": 1.086, "l": 1.079, "c": 1.085, "v": 0},
        {"t": "2026-08-03", "o": 1.085, "h": 1.091, "l": 1.084, "c": 1.090, "v": 0},
    ],
}


def _fake_daily_bars(symbol, start, end):
    return BARS.get(symbol, [])


def test_no_trades_curve_stays_flat_at_initial_capital(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_forex_daily_bars", _fake_daily_bars)
    code = """
def decide_forex(as_of, positions, quotes, account):
    return []
"""
    curve = run_backtest(code, ["EURUSD=X"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert [p["equity"] for p in curve] == [1000.0, 1000.0, 1000.0]
    assert [p["date"] for p in curve] == ["2026-08-01", "2026-08-02", "2026-08-03"]


def test_buy_and_hold_tracks_mark_to_market(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_forex_daily_bars", _fake_daily_bars)
    code = """
def decide_forex(as_of, positions, quotes, account):
    if as_of == "2026-08-01" and not positions:
        return [{"action": "open", "symbol": "EURUSD=X", "side": "buy", "qty": 500}]
    return []
"""
    curve = run_backtest(code, ["EURUSD=X"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    # Entry day is breakeven (cash outflow exactly offset by same-day mark),
    # then equity tracks the position's own daily close 1:1 -- same
    # entry-day-breakeven subtlety domain/options/backtester.py's tests
    # pinned down.
    assert curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert curve[1]["equity"] == pytest.approx(1000.0 + 500 * (1.085 - 1.080), abs=1e-6)
    assert curve[2]["equity"] == pytest.approx(1000.0 + 500 * (1.090 - 1.080), abs=1e-6)


def test_short_position_profits_when_price_falls(monkeypatch):
    def falling_bars(symbol, start, end):
        return [
            {"t": "2026-08-01", "o": 1.080, "h": 1.081, "l": 1.079, "c": 1.080, "v": 0},
            {"t": "2026-08-02", "o": 1.080, "h": 1.080, "l": 1.070, "c": 1.075, "v": 0},
        ]

    monkeypatch.setattr(backtester_module, "get_forex_daily_bars", falling_bars)
    code = """
def decide_forex(as_of, positions, quotes, account):
    if as_of == "2026-08-01" and not positions:
        return [{"action": "open", "symbol": "EURUSD=X", "side": "sell", "qty": 500}]
    return []
"""
    curve = run_backtest(code, ["EURUSD=X"], date(2026, 8, 1), date(2026, 8, 3), 1000.0)
    assert curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert curve[1]["equity"] == pytest.approx(1000.0 + 500 * (1.080 - 1.075), abs=1e-6)


def test_open_then_close_realizes_pnl_and_flattens_position(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_forex_daily_bars", _fake_daily_bars)
    code = """
def decide_forex(as_of, positions, quotes, account):
    if as_of == "2026-08-01" and not positions:
        return [{"action": "open", "symbol": "EURUSD=X", "side": "buy", "qty": 500}]
    if as_of == "2026-08-02" and positions:
        return [{"action": "close", "symbol": "EURUSD=X", "side": "sell", "qty": 500}]
    return []
"""
    curve = run_backtest(code, ["EURUSD=X"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert curve[1]["equity"] == pytest.approx(1000.0 + 500 * (1.085 - 1.080), abs=1e-6)
    assert curve[2]["equity"] == pytest.approx(curve[1]["equity"], abs=1e-6)


def test_strategy_receives_prev_close_from_the_prior_trading_day(monkeypatch):
    """Baked in from the start this time (see backtester.py's docstring) --
    a live-verification run against the Futures build caught the equivalent
    bug there after shipping."""
    monkeypatch.setattr(backtester_module, "get_forex_daily_bars", _fake_daily_bars)
    trigger_code = """
def decide_forex(as_of, positions, quotes, account):
    q = quotes.get("EURUSD=X", {})
    if not positions and q.get("prev_close") is not None:
        return [{"action": "open", "symbol": "EURUSD=X", "side": "buy", "qty": 500}]
    return []
"""
    curve = run_backtest(trigger_code, ["EURUSD=X"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert curve[1]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert curve[2]["equity"] == pytest.approx(1000.0 + 500 * (1.090 - 1.085), abs=1e-6)


def test_open_exceeding_cash_is_refused(monkeypatch):
    """Baked in from the start (see backtester.py's docstring) -- 500 units
    of a pair priced above $2/unit would cost more than the $1,000 wallet."""
    def expensive_bars(symbol, start, end):
        return [
            {"t": "2026-08-01", "o": 2.50, "h": 2.51, "l": 2.49, "c": 2.50, "v": 0},
            {"t": "2026-08-02", "o": 2.50, "h": 2.60, "l": 2.49, "c": 2.55, "v": 0},
        ]

    monkeypatch.setattr(backtester_module, "get_forex_daily_bars", expensive_bars)
    code = """
def decide_forex(as_of, positions, quotes, account):
    if not positions:
        return [{"action": "open", "symbol": "EURUSD=X", "side": "buy", "qty": 500}]
    return []
"""
    curve = run_backtest(code, ["EURUSD=X"], date(2026, 8, 1), date(2026, 8, 3), 1000.0)
    assert [p["equity"] for p in curve] == [1000.0, 1000.0]


def test_missing_bars_returns_empty_curve(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_forex_daily_bars", lambda symbol, start, end: [])
    code = """
def decide_forex(as_of, positions, quotes, account):
    return []
"""
    curve = run_backtest(code, ["EURUSD=X"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert curve == []
