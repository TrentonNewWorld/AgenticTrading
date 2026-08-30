"""Futures market data for the Futures dashboard -- free Yahoo Finance
continuous-contract data via ``yfinance``, no API key and no Alpaca
credentials required (Alpaca doesn't support futures at all).

Chosen after live-spiking the alternative: Tradovate's own market/trading API
turns out to require either a $1,000+ funded account plus monthly fees, or a
formal NinjaTrader-Ecosystem partner application -- not the free self-serve
demo Alpaca/OANDA both offer, contradicting the assumption the original
5-dashboard plan made when it grouped Tradovate with OANDA. Confirmed by a
live spike against the real Yahoo endpoint (2026-08-23): 502 daily bars (~2
years) for every symbol below, and reliable current pricing via a short
recent-history fetch. Real Tradovate order execution is wired in at
``infrastructure/brokers/tradovate_paper.py`` but gated off by default (see
that module's docstring) -- this module is what powers the simulated paper
wallet everyone gets without any broker signup at all.

Yahoo's own continuous-contract symbols are used as-is throughout this
dashboard (``ES=F``, ``MES=F``, ...) rather than inventing a synthetic format
the way Options' OCC symbols were needed -- there is no per-contract-month
chain to enumerate here, Yahoo already rolls the front month forward.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

#: A small, liquid, diversified default universe -- one or two symbols per
#: major futures category (equity index, metals, energy, rates) rather than
#: an exhaustive list, mirroring domain/options/engine.py's own
#: DEFAULT_OPTIONS_UNIVERSE decision to keep every fetch/backtest fast. Every
#: symbol here was confirmed to return real data in the 2026-08-23 spike;
#: MCL=F (Micro WTI Crude) was tried and dropped -- Yahoo has no reliable feed
#: for it, unlike its equity-index and metals micro siblings.
FUTURES_UNIVERSE = ["ES=F", "MES=F", "NQ=F", "GC=F", "CL=F", "ZN=F"]

FUTURES_DISPLAY_NAMES = {
    "ES=F": "E-mini S&P 500",
    "MES=F": "Micro E-mini S&P 500",
    "NQ=F": "E-mini Nasdaq-100",
    "MNQ=F": "Micro E-mini Nasdaq-100",
    "GC=F": "Gold",
    "MGC=F": "Micro Gold",
    "SI=F": "Silver",
    "CL=F": "Crude Oil (WTI)",
    "NG=F": "Natural Gas",
    "ZN=F": "10-Year T-Note",
    "ZC=F": "Corn",
}


class MarketDataUnavailableError(RuntimeError):
    """Yahoo returned nothing usable for a symbol -- the caller decides
    whether that means "skip this tick" (engine) or "fail the request"
    (an explicit single-symbol lookup)."""


def _yf():
    import yfinance as yf

    return yf


def get_futures_quotes_batch(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Latest close + previous close for each symbol, via one batched
    5-day-daily download. fast_info returns None for every futures symbol
    (confirmed in the spike) -- Yahoo simply doesn't populate it for the
    futures asset class -- so this is the *primary* path here, not a
    fallback, unlike quotes.py's stocks version."""
    if not symbols:
        return {}
    yf = _yf()
    try:
        hist = yf.download(
            symbols, period="5d", interval="1d", group_by="ticker",
            auto_adjust=True, progress=False, threads=True,
        )
    except Exception as exc:
        print(f"yahoo_futures: batch quote fetch failed: {exc}")
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
            print(f"yahoo_futures: {symbol}: quote parse failed: {exc}")
    return out


def get_futures_quote(symbol: str) -> Dict[str, Any]:
    quotes = get_futures_quotes_batch([symbol])
    if symbol not in quotes:
        raise MarketDataUnavailableError(f"no Yahoo Finance quote available for {symbol}")
    return quotes[symbol]


def get_futures_daily_bars(symbol: str, start: str, end: str) -> List[Dict[str, Any]]:
    """Daily OHLCV bars for the backtester, ``start``/``end`` as YYYY-MM-DD
    (end exclusive, matching yfinance's own convention)."""
    yf = _yf()
    try:
        hist = yf.download(symbol, start=start, end=end, interval="1d", auto_adjust=True, progress=False)
    except Exception as exc:
        raise MarketDataUnavailableError(f"Yahoo Finance history fetch failed for {symbol}: {exc}") from exc
    if hist is None or hist.empty:
        raise MarketDataUnavailableError(f"no Yahoo Finance daily bars for {symbol} in [{start}, {end})")

    # A single-symbol yf.download() still returns MultiIndex columns
    # (('Open', 'ES=F'), ...) in this yfinance version -- hist[col] is then
    # itself a 1-column DataFrame, not a Series, so a plain float(row[col])
    # emits (and will eventually fail on) a "single element Series" warning.
    # Dropping the ticker level once, up front, is simpler than converting
    # every field on every row.
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
    return FUTURES_DISPLAY_NAMES.get(symbol, symbol)
