"""Crypto market data for the Crypto dashboard -- Alpaca's own crypto data
API, confirmed live-working in this dev environment (2026-08-23 spike: real
credentials, 730 daily bars over 2 full years for BTC/USD with zero gaps --
crypto trades every calendar day, unlike equities/futures/forex, so there is
no weekend-closure bookkeeping to do at all).

Unlike Futures/Forex, this dashboard does NOT default to a free/simulated
data source: Alpaca is already the broker every other dashboard in this app
uses, the same credentials already work for crypto, and Alpaca's crypto
market data endpoints need no separate approval (confirmed: a plain,
unauthenticated ``CryptoHistoricalDataClient()`` even returns live data,
though this module still passes credentials through when available for
consistency and any account-scoped subscription tier). See
infrastructure/brokers/alpaca_paper_crypto.py's docstring for the matching
real (not gated-off-by-default) broker client built on the same footing.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

#: A diversified default universe of major, liquid, Alpaca-tradable USD-quote
#: coins -- confirmed tradable and fractionable against a real paper account
#: in the 2026-08-23 spike. Every pair here is USD-quoted (like Futures/
#: Forex's own universe restrictions), so qty * price is always a clean USD
#: notional with no base/quote currency split to worry about.
CRYPTO_UNIVERSE = ["BTC/USD", "ETH/USD", "SOL/USD", "LTC/USD", "LINK/USD", "DOGE/USD"]

CRYPTO_DISPLAY_NAMES = {
    "BTC/USD": "Bitcoin", "ETH/USD": "Ethereum", "SOL/USD": "Solana",
    "LTC/USD": "Litecoin", "LINK/USD": "Chainlink", "DOGE/USD": "Dogecoin",
}


class MarketDataUnavailableError(RuntimeError):
    """Alpaca returned nothing usable for a symbol."""


def _client(api_key: Optional[str] = None, secret_key: Optional[str] = None):
    from alpaca.data.historical.crypto import CryptoHistoricalDataClient

    if api_key and secret_key:
        return CryptoHistoricalDataClient(api_key, secret_key)
    return CryptoHistoricalDataClient()


def get_crypto_quotes_batch(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Latest quote (bid/ask midpoint) + previous day's close for each
    symbol."""
    if not symbols:
        return {}
    from alpaca.data.requests import CryptoLatestQuoteRequest

    try:
        client = _client()
        quotes = client.get_crypto_latest_quote(CryptoLatestQuoteRequest(symbol_or_symbols=symbols))
    except Exception as exc:
        print(f"alpaca_crypto: batch quote fetch failed: {exc}")
        return {}

    prev_closes = _previous_closes(symbols)
    out: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for symbol in symbols:
        quote = quotes.get(symbol)
        if quote is None:
            continue
        bid, ask = getattr(quote, "bid_price", None), getattr(quote, "ask_price", None)
        if bid and ask:
            price = (bid + ask) / 2
        elif ask:
            price = ask
        elif bid:
            price = bid
        else:
            continue
        out[symbol] = {"symbol": symbol, "price": float(price), "prev_close": prev_closes.get(symbol), "timestamp": now_iso}
    return out


def _previous_closes(symbols: List[str]) -> Dict[str, float]:
    """Yesterday's close for each symbol, via a short recent daily-bar
    fetch -- crypto trades every day, so this is always literally
    yesterday, no weekend/holiday skip needed."""
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from datetime import timedelta

    try:
        client = _client()
        req = CryptoBarsRequest(
            symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
            start=datetime.now(timezone.utc) - timedelta(days=5),
        )
        bars = client.get_crypto_bars(req).df
    except Exception as exc:
        print(f"alpaca_crypto: previous-close fetch failed: {exc}")
        return {}

    closes: Dict[str, float] = {}
    if bars is None or bars.empty:
        return closes
    for symbol in symbols:
        try:
            symbol_bars = bars.xs(symbol, level="symbol") if len(symbols) > 1 or "symbol" in bars.index.names else bars
            sorted_closes = symbol_bars["close"].sort_index()
            if len(sorted_closes) >= 2:
                closes[symbol] = float(sorted_closes.iloc[-2])
        except (KeyError, IndexError):
            continue
    return closes


def get_crypto_daily_bars(symbol: str, start: str, end: str) -> List[Dict[str, Any]]:
    """Daily OHLCV bars for the backtester, start/end as YYYY-MM-DD."""
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame

    try:
        client = _client()
        req = CryptoBarsRequest(
            symbol_or_symbols=[symbol], timeframe=TimeFrame.Day,
            start=datetime.fromisoformat(start), end=datetime.fromisoformat(end),
        )
        bars = client.get_crypto_bars(req).df
    except Exception as exc:
        raise MarketDataUnavailableError(f"Alpaca crypto history fetch failed for {symbol}: {exc}") from exc

    if bars is None or bars.empty:
        raise MarketDataUnavailableError(f"no Alpaca crypto daily bars for {symbol} in [{start}, {end})")

    try:
        symbol_bars = bars.xs(symbol, level="symbol")
    except KeyError:
        raise MarketDataUnavailableError(f"no Alpaca crypto daily bars for {symbol} in [{start}, {end})")

    out: List[Dict[str, Any]] = []
    for ts, row in symbol_bars.sort_index().iterrows():
        try:
            o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        except (TypeError, ValueError, KeyError):
            continue
        volume = row.get("volume")
        out.append({
            "t": ts.strftime("%Y-%m-%d"),
            "o": o, "h": h, "l": l, "c": c,
            "v": float(volume) if volume is not None else 0.0,
        })
    return out


def display_name(symbol: str) -> str:
    return CRYPTO_DISPLAY_NAMES.get(symbol, symbol)
