"""Forex market data for the Forex dashboard -- free Yahoo Finance rates via
``yfinance``, no API key required. Mirrors infrastructure/market_data/
yahoo_futures.py's shape and reasoning (see that module's docstring for why
this dashboard defaults to a free data source rather than requiring the
operator to provision broker credentials up front). Confirmed by a live
spike (2026-08-23): 518 daily bars (~2 years) and reliable current pricing
via the same short recent-history fetch.

Real OANDA order execution is wired in at infrastructure/brokers/
oanda_practice.py but gated off by default (see that module's docstring) --
this module is what powers the simulated paper wallet everyone gets without
a broker signup, same posture as Futures.

Universe restricted to USD-quote pairs (EUR/USD, GBP/USD, AUD/USD, NZD/USD)
on purpose: this dashboard's wallet is USD, and for a "XXX/USD" pair, buying
``qty`` units of the base currency costs ``qty * price`` USD -- clean and
consistent with how domain/futures/backtester.py already prices a position.
A "USD/XXX" pair (USD/JPY, USD/CAD, USD/CHF) has USD as the BASE currency
instead, where cost would be ``qty`` USD regardless of price, not
``qty * price`` -- a real, forex-specific unit-convention split that doesn't
exist for stocks/options/futures. Rather than branch every cost calculation
on which side of the pair USD sits (an easy place to get the sign or the
formula wrong with no live broker to catch it against), the starter roster
and default universe simply stay on the side where the existing qty * price
math is already correct.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

#: Every symbol here is a USD-quote pair ("XXX/USD") -- see module docstring
#: for why that's a hard requirement, not just a starting default.
FOREX_UNIVERSE = ["EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"]

FOREX_DISPLAY_NAMES = {
    "EURUSD=X": "Euro / US Dollar",
    "GBPUSD=X": "British Pound / US Dollar",
    "AUDUSD=X": "Australian Dollar / US Dollar",
    "NZDUSD=X": "New Zealand Dollar / US Dollar",
}


class MarketDataUnavailableError(RuntimeError):
    """Yahoo returned nothing usable for a symbol."""


def _yf():
    import yfinance as yf

    return yf


def get_forex_quotes_batch(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Latest close + previous close for each symbol, via one batched
    5-day-daily download -- same reasoning as yahoo_futures.py's equivalent:
    fast_info doesn't reliably populate for this asset class either."""
    if not symbols:
        return {}
    yf = _yf()
    try:
        hist = yf.download(
            symbols, period="5d", interval="1d", group_by="ticker",
            auto_adjust=True, progress=False, threads=True,
        )
    except Exception as exc:
        print(f"yahoo_forex: batch quote fetch failed: {exc}")
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for symbol in symbols:
        try:
            closes = (hist[symbol]["Close"].dropna() if len(symbols) > 1 else hist["Close"].dropna())
            if closes.empty:
                continue
            price = float(closes.iloc[-1])
            prev_close = float(closes.iloc[-2]) if len(closes) >= 2 else None
            out[symbol] = {"symbol": symbol, "price": price, "prev_close": prev_close, "timestamp": now_iso}
        except Exception as exc:
            print(f"yahoo_forex: {symbol}: quote parse failed: {exc}")
    return out


def get_forex_quote(symbol: str) -> Dict[str, Any]:
    quotes = get_forex_quotes_batch([symbol])
    if symbol not in quotes:
        raise MarketDataUnavailableError(f"no Yahoo Finance quote available for {symbol}")
    return quotes[symbol]


def get_forex_daily_bars(symbol: str, start: str, end: str) -> List[Dict[str, Any]]:
    """Daily OHLC bars for the backtester, start/end as YYYY-MM-DD (end
    exclusive, matching yfinance's own convention)."""
    yf = _yf()
    try:
        hist = yf.download(symbol, start=start, end=end, interval="1d", auto_adjust=True, progress=False)
    except Exception as exc:
        raise MarketDataUnavailableError(f"Yahoo Finance history fetch failed for {symbol}: {exc}") from exc
    if hist is None or hist.empty:
        raise MarketDataUnavailableError(f"no Yahoo Finance daily bars for {symbol} in [{start}, {end})")

    import pandas as pd

    if isinstance(hist.columns, pd.MultiIndex):
        hist = hist.droplevel(1, axis=1)

    bars: List[Dict[str, Any]] = []
    for ts, row in hist.iterrows():
        try:
            o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        except (TypeError, ValueError, KeyError):
            continue
        volume = row.get("Volume")
        bars.append({
            "t": ts.strftime("%Y-%m-%d"),
            "o": o, "h": h, "l": l, "c": c,
            "v": float(volume) if volume is not None else 0.0,
        })
    return bars


def display_name(symbol: str) -> str:
    return FOREX_DISPLAY_NAMES.get(symbol, symbol)
