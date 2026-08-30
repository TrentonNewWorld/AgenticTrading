"""Fetches the most recent completed year of real Alpaca daily bars and
simulates an already-scanned strategy's decide() over them, starting from a
$1,000 wallet. See ``simulator.py`` for the actual day-by-day loop -- this
module just supplies the data and the window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dashboard.backend.domain.leaderboard.catalog import _fetch_daily_bars
from dashboard.backend.domain.strategy_testing.report import build_report
from dashboard.backend.domain.strategy_testing.simulator import simulate
from dashboard.backend.infrastructure.llm.validator import DJIA_30

INITIAL_CAPITAL = 1000.0
#: +30d buffer before the 1-year test window so decide() has some pre-window
#: history to work with on day 1, not just a single day's close.
_LOOKBACK_DAYS = 365 + 30


def run_backtest(code: str, *, initial_capital: float = INITIAL_CAPITAL, timeout_per_day: int = 10) -> dict:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start_test = end - timedelta(days=365)

    bars_by_symbol = _fetch_daily_bars(DJIA_30, end, lookback_days=_LOOKBACK_DAYS)
    if not bars_by_symbol:
        raise RuntimeError("no market data available (check ALPACA_API_KEY/ALPACA_SECRET_KEY)")

    all_dates = sorted({d.date() for df in bars_by_symbol.values() for d in df.index})
    test_dates = [d for d in all_dates if d >= start_test.date()]
    if len(test_dates) < 5:
        raise RuntimeError("not enough trading days of market data to backtest")

    curve = simulate(code, bars_by_symbol, test_dates, initial_capital, timeout_per_day=timeout_per_day)
    report = build_report(curve, initial_capital)
    report["window"] = {"start_date": test_dates[0].isoformat(), "end_date": test_dates[-1].isoformat()}
    report["n_decisions"] = len(curve)
    return report


def run_options_backtest(code: str, *, initial_capital: float = INITIAL_CAPITAL) -> dict:
    """Options counterpart of ``run_backtest``: same $1,000-wallet,
    most-recent-completed-year convention, but delegates the actual
    day-by-day simulation to ``domain.options.backtester`` (contract legs,
    not share weights -- see that module's docstring for the expiration/
    settlement model) instead of ``simulator.simulate``.

    Uses ``domain.options.engine.DEFAULT_OPTIONS_UNIVERSE`` as the
    underlying universe -- Options strategies don't declare their own (no
    upload-time field for it, matching this module's own DJIA_30 default
    for stocks)."""
    from dashboard.backend.domain.options.backtester import run_backtest as run_options_curve
    from dashboard.backend.domain.options.engine import DEFAULT_OPTIONS_UNIVERSE
    from dashboard.backend.domain.options.report import build_report as build_options_report

    end = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    start_test = end - timedelta(days=365)

    curve = run_options_curve(code, DEFAULT_OPTIONS_UNIVERSE, start_test, end, initial_capital)
    if not curve:
        raise RuntimeError(
            "no options market data available for the backtest window "
            "(check ALPACA_API_KEY/ALPACA_SECRET_KEY, or no listed contracts "
            "were found for the default underlying universe)"
        )
    if len(curve) < 5:
        raise RuntimeError("not enough trading days of options market data to backtest")

    report = build_options_report(curve, initial_capital)
    report["window"] = {"start_date": curve[0]["date"], "end_date": curve[-1]["date"]}
    report["n_decisions"] = len(curve)
    return report


def run_futures_backtest(code: str, *, initial_capital: float = INITIAL_CAPITAL) -> dict:
    """Futures counterpart of ``run_backtest``: same $1,000-wallet,
    most-recent-completed-year convention, delegating to
    ``domain.futures.backtester`` (single-instrument daily mark-to-market on
    free Yahoo Finance data, see that module's docstring)."""
    from dashboard.backend.domain.futures.backtester import run_backtest as run_futures_curve
    from dashboard.backend.domain.futures.report import build_report as build_futures_report
    from dashboard.backend.infrastructure.market_data.yahoo_futures import FUTURES_UNIVERSE

    end = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    start_test = end - timedelta(days=365)

    curve = run_futures_curve(code, FUTURES_UNIVERSE, start_test, end, initial_capital)
    if not curve:
        raise RuntimeError("no futures market data available for the backtest window (Yahoo Finance fetch failed)")
    if len(curve) < 5:
        raise RuntimeError("not enough trading days of futures market data to backtest")

    report = build_futures_report(curve, initial_capital)
    report["window"] = {"start_date": curve[0]["date"], "end_date": curve[-1]["date"]}
    report["n_decisions"] = len(curve)
    return report


def run_forex_backtest(code: str, *, initial_capital: float = INITIAL_CAPITAL) -> dict:
    """Forex counterpart of ``run_backtest``: same $1,000-wallet,
    most-recent-completed-year convention, delegating to
    ``domain.forex.backtester`` (single-instrument daily mark-to-market on
    free Yahoo Finance data, see that module's docstring)."""
    from dashboard.backend.domain.forex.backtester import run_backtest as run_forex_curve
    from dashboard.backend.domain.forex.report import build_report as build_forex_report
    from dashboard.backend.infrastructure.market_data.yahoo_forex import FOREX_UNIVERSE

    end = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    start_test = end - timedelta(days=365)

    curve = run_forex_curve(code, FOREX_UNIVERSE, start_test, end, initial_capital)
    if not curve:
        raise RuntimeError("no forex market data available for the backtest window (Yahoo Finance fetch failed)")
    if len(curve) < 5:
        raise RuntimeError("not enough trading days of forex market data to backtest")

    report = build_forex_report(curve, initial_capital)
    report["window"] = {"start_date": curve[0]["date"], "end_date": curve[-1]["date"]}
    report["n_decisions"] = len(curve)
    return report


def run_crypto_backtest(code: str, *, initial_capital: float = INITIAL_CAPITAL) -> dict:
    """Crypto counterpart of ``run_backtest``: same $1,000-wallet,
    most-recent-completed-year convention, delegating to
    ``domain.crypto.backtester`` (single-instrument daily mark-to-market on
    real Alpaca crypto data, see that module's docstring)."""
    from dashboard.backend.domain.crypto.backtester import run_backtest as run_crypto_curve
    from dashboard.backend.domain.crypto.report import build_report as build_crypto_report
    from dashboard.backend.infrastructure.market_data.alpaca_crypto import CRYPTO_UNIVERSE

    end = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    start_test = end - timedelta(days=365)

    curve = run_crypto_curve(code, CRYPTO_UNIVERSE, start_test, end, initial_capital)
    if not curve:
        raise RuntimeError("no crypto market data available for the backtest window (Alpaca crypto data fetch failed)")
    if len(curve) < 5:
        raise RuntimeError("not enough trading days of crypto market data to backtest")

    report = build_crypto_report(curve, initial_capital)
    report["window"] = {"start_date": curve[0]["date"], "end_date": curve[-1]["date"]}
    report["n_decisions"] = len(curve)
    return report
