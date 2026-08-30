from __future__ import annotations

from datetime import date

import pytest

import dashboard.backend.domain.crypto.backtester as backtester_module
from dashboard.backend.domain.crypto.backtester import run_backtest

BARS = {
    "BTC/USD": [
        {"t": "2026-08-01", "o": 77000, "h": 77200, "l": 76800, "c": 77000, "v": 0},
        {"t": "2026-08-02", "o": 77000, "h": 78200, "l": 76900, "c": 78000, "v": 0},
        {"t": "2026-08-03", "o": 78000, "h": 79200, "l": 77800, "c": 79000, "v": 0},
    ],
}


def _fake_daily_bars(symbol, start, end):
    return BARS.get(symbol, [])


def test_no_trades_curve_stays_flat_at_initial_capital(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", _fake_daily_bars)
    code = """
def decide_crypto(as_of, positions, quotes, account):
    return []
"""
    curve = run_backtest(code, ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert [p["equity"] for p in curve] == [1000.0, 1000.0, 1000.0]
    assert [p["date"] for p in curve] == ["2026-08-01", "2026-08-02", "2026-08-03"]


def test_buy_and_hold_tracks_mark_to_market_with_fractional_qty(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", _fake_daily_bars)
    code = """
def decide_crypto(as_of, positions, quotes, account):
    if as_of == "2026-08-01" and not positions:
        return [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.01}]
    return []
"""
    curve = run_backtest(code, ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert curve[1]["equity"] == pytest.approx(1000.0 + 0.01 * (78000 - 77000), abs=1e-6)
    assert curve[2]["equity"] == pytest.approx(1000.0 + 0.01 * (79000 - 77000), abs=1e-6)


def test_short_position_profits_when_price_falls(monkeypatch):
    def falling_bars(symbol, start, end):
        return [
            {"t": "2026-08-01", "o": 77000, "h": 77200, "l": 76800, "c": 77000, "v": 0},
            {"t": "2026-08-02", "o": 77000, "h": 77000, "l": 75000, "c": 75500, "v": 0},
        ]

    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", falling_bars)
    code = """
def decide_crypto(as_of, positions, quotes, account):
    if as_of == "2026-08-01" and not positions:
        return [{"action": "open", "symbol": "BTC/USD", "side": "sell", "qty": 0.01}]
    return []
"""
    curve = run_backtest(code, ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 3), 1000.0)
    assert curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert curve[1]["equity"] == pytest.approx(1000.0 + 0.01 * (77000 - 75500), abs=1e-6)


def test_open_then_close_realizes_pnl_and_flattens_position(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", _fake_daily_bars)
    code = """
def decide_crypto(as_of, positions, quotes, account):
    if as_of == "2026-08-01" and not positions:
        return [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.01}]
    if as_of == "2026-08-02" and positions:
        return [{"action": "close", "symbol": "BTC/USD", "side": "sell", "qty": 0.01}]
    return []
"""
    curve = run_backtest(code, ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert curve[1]["equity"] == pytest.approx(1000.0 + 0.01 * (78000 - 77000), abs=1e-6)
    assert curve[2]["equity"] == pytest.approx(curve[1]["equity"], abs=1e-6)


def test_strategy_receives_prev_close_from_the_prior_trading_day(monkeypatch):
    """Baked in from the start (see backtester.py's docstring) -- the same
    bug caught in the Futures build after shipping."""
    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", _fake_daily_bars)
    trigger_code = """
def decide_crypto(as_of, positions, quotes, account):
    q = quotes.get("BTC/USD", {})
    if not positions and q.get("prev_close") is not None:
        return [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.01}]
    return []
"""
    curve = run_backtest(trigger_code, ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert curve[1]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert curve[2]["equity"] == pytest.approx(1000.0 + 0.01 * (79000 - 78000), abs=1e-6)


def test_open_exceeding_cash_is_refused(monkeypatch):
    """Baked in from the start (see backtester.py's docstring) -- 1 whole
    BTC at a real price would cost ~$77,000 against a $1,000 wallet."""
    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", _fake_daily_bars)
    code = """
def decide_crypto(as_of, positions, quotes, account):
    if not positions:
        return [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 1}]
    return []
"""
    curve = run_backtest(code, ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert [p["equity"] for p in curve] == [1000.0, 1000.0, 1000.0]


def test_missing_bars_returns_empty_curve(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", lambda symbol, start, end: [])
    code = """
def decide_crypto(as_of, positions, quotes, account):
    return []
"""
    curve = run_backtest(code, ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 4), 1000.0)
    assert curve == []
