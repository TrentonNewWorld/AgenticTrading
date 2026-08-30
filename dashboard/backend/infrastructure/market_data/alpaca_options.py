"""Alpaca options market-data helper.

Sub-phase 4 of the Options-dashboard plan. Options contract symbology and
history work differently enough from equities (``infrastructure/market_data/
alpaca_bars.py``) to be its own module rather than a parameter on that one:

* Alpaca's options chain/contracts endpoints are **current-state snapshots**
  only -- there is no "give me the chain as it looked on 2025-03-14" query.
  A contract-level backtest therefore cannot ask "what existed then"; it has
  to *synthesize* the OCC symbol a listed contract would have used (a fixed,
  deterministic format -- underlying + expiration + right + strike, no API
  call needed) and then check whether ``get_option_bars`` actually has
  history for it. See ``dashboard/backend/scripts/spike_options_data_findings.md``
  for the confirmation this works and how far back it reaches.
* Options SDK classes (``OptionHistoricalDataClient``, ``OptionBarsRequest``,
  ``OptionChainRequest``) are entirely separate from the equities ones
  (``StockHistoricalDataClient``, ``StockBarsRequest``) already used
  everywhere else in this repo -- no shared base class to parameterize.

Alpaca-py imports are lazy (inside functions), matching alpaca_bars.py's
convention, so importing this module performs no network requests and never
requires the SDK to be installed just to import it.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

#: Default underlying(s) for a My Agents options agent backtest when the
#: agent's config doesn't name any -- matches domain/options/catalog.py's own
#: existing ["SPY"] fallback for a starter strategy with no configured
#: underlyings, so the two defaults don't drift apart.
OPTIONS_UNDERLYING_UNIVERSE = ["SPY"]

# OCC option symbol: 1-6 char root, YYMMDD expiration, C/P, 8-digit strike*1000
# (zero-padded). e.g. "AAPL260918C00185000" = AAPL, 2026-09-18, Call, $185.00.
_OCC_PATTERN = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


class OptionSymbolError(ValueError):
    """Raised by synthesize/parse on a malformed underlying, right, or symbol."""


def synthesize_occ_symbol(underlying: str, expiration: date, right: str, strike: float) -> str:
    """Build the OCC symbol a listed contract with these terms would use.

    Deterministic, no API call -- this is what lets the contract-candidate
    enumerator (``domain/options/contracts.py``) generate historical symbols
    to probe, since Alpaca cannot be asked "what was listed on this date"."""
    right = right.strip().upper()
    if right not in ("C", "P"):
        raise OptionSymbolError(f"right must be 'C' or 'P', got {right!r}")
    if strike <= 0:
        raise OptionSymbolError(f"strike must be positive, got {strike!r}")
    underlying = underlying.strip().upper()
    if not underlying or not underlying.isalpha() or len(underlying) > 6:
        raise OptionSymbolError(f"invalid underlying symbol: {underlying!r}")
    strike_int = round(strike * 1000)
    return f"{underlying}{expiration.strftime('%y%m%d')}{right}{strike_int:08d}"


def parse_occ_symbol(symbol: str) -> Dict[str, Any]:
    """Inverse of :func:`synthesize_occ_symbol`.

    Returns ``{underlying, expiration (date), right ('C'|'P'), strike (float)}``.
    Raises :class:`OptionSymbolError` on anything not matching the OCC shape.
    """
    match = _OCC_PATTERN.match(symbol.strip().upper())
    if not match:
        raise OptionSymbolError(f"not a valid OCC option symbol: {symbol!r}")
    underlying, date_str, right, strike_str = match.groups()
    try:
        expiration = datetime.strptime(date_str, "%y%m%d").date()
    except ValueError as exc:
        raise OptionSymbolError(f"invalid expiration in symbol {symbol!r}: {exc}") from exc
    return {
        "underlying": underlying,
        "expiration": expiration,
        "right": right,
        "strike": int(strike_str) / 1000,
    }


def _credentials() -> tuple[str, str]:
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise MarketDataUnavailableError(
            "ALPACA_API_KEY / ALPACA_SECRET_KEY not set -- required for options market data."
        )
    return api_key, secret_key


class MarketDataUnavailableError(RuntimeError):
    """Raised when options market data cannot be fetched (missing credentials,
    SDK, or a request failure) -- mirrors alpaca_bars.py's exception, kept
    separate since the two data sources have no shared failure surface."""


def get_option_bars(
    symbols: List[str],
    start: date,
    end: date,
) -> Dict[str, pd.DataFrame]:
    """Daily bars per OCC symbol, for whichever of ``symbols`` actually have
    history in ``[start, end]``. Missing symbols are simply absent from the
    result -- callers must treat an absent symbol as "no data", not an error,
    since most synthesized candidate symbols in a given window will not have
    been real listed contracts (see the module docstring)."""
    if not symbols:
        return {}
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionBarsRequest
        from alpaca.data.timeframe import TimeFrame

        api_key, secret_key = _credentials()
        client = OptionHistoricalDataClient(api_key, secret_key)
        response = client.get_option_bars(
            OptionBarsRequest(
                symbol_or_symbols=list(symbols),
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
            )
        )
    except MarketDataUnavailableError:
        raise
    except Exception as exc:  # pragma: no cover - defensive, mirrors alpaca_bars.py
        raise MarketDataUnavailableError(f"get_option_bars failed: {exc}") from exc

    data = getattr(response, "data", {}) or {}
    result: Dict[str, pd.DataFrame] = {}
    for symbol, bars in data.items():
        if not bars:
            continue
        rows = [
            {
                "t": bar.timestamp,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume) if bar.volume is not None else 0.0,
            }
            for bar in bars
        ]
        frame = pd.DataFrame(rows)
        frame["t"] = pd.to_datetime(frame["t"])
        result[symbol] = frame.set_index("t")
    return result


def get_option_chain_snapshot(underlying: str, *, feed: Optional[str] = None) -> Dict[str, Any]:
    """The **current** option chain for ``underlying`` -- symbol, strike,
    expiration, right, bid/ask/last for every live contract.

    Current-state only: there is no historical equivalent (see module
    docstring). Used to build the live decision-cycle ``chain`` argument the
    ``decide_options`` contract takes (domain/options/sandbox.py) -- never
    for backtesting, which instead probes get_option_bars for synthesized
    historical symbols."""
    try:
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionChainRequest

        api_key, secret_key = _credentials()
        client = OptionHistoricalDataClient(api_key, secret_key)
        feed_enum = OptionsFeed(feed) if feed else OptionsFeed.INDICATIVE
        chain = client.get_option_chain(
            OptionChainRequest(underlying_symbol=underlying.strip().upper(), feed=feed_enum)
        )
    except MarketDataUnavailableError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise MarketDataUnavailableError(f"get_option_chain_snapshot failed: {exc}") from exc

    snapshot: Dict[str, Any] = {}
    for symbol, contract in (chain or {}).items():
        try:
            parsed = parse_occ_symbol(symbol)
        except OptionSymbolError:
            continue
        quote = getattr(contract, "latest_quote", None)
        trade = getattr(contract, "latest_trade", None)
        snapshot[symbol] = {
            "symbol": symbol,
            "underlying": parsed["underlying"],
            "expiration": parsed["expiration"].isoformat(),
            "right": parsed["right"],
            "strike": parsed["strike"],
            "bid": float(quote.bid_price) if quote and quote.bid_price else None,
            "ask": float(quote.ask_price) if quote and quote.ask_price else None,
            "last": float(trade.price) if trade and trade.price else None,
        }
    return snapshot
