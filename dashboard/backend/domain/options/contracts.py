"""Candidate-contract enumerator for Options backtesting (Sub-phase 4 of the
Options-dashboard plan).

Alpaca's options chain/contracts endpoints are current-state snapshots only
-- there is no "what existed on this past date" query (see
infrastructure/market_data/alpaca_options.py's module docstring). A full-year
contract-level backtest therefore cannot ask Alpaca what was listed; instead
this module *synthesizes* plausible OCC symbols (standard monthly
expirations, a strike grid tracking the underlying's own historical price)
and probes ``get_option_bars`` to find which were real listed contracts with
history, caching the result (``options_contract_cache``) so a re-run doesn't
re-probe Alpaca for contracts already confirmed to exist or not.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from dashboard.backend.database import DB_PATH
from dashboard.backend.infrastructure.market_data.alpaca_options import (
    MarketDataUnavailableError,
    get_option_bars,
    synthesize_occ_symbol,
)

# Alpaca's option bars endpoint accepts a symbol list, but a single-request
# batch of a few hundred synthesized candidates risks an oversized request --
# probe in chunks instead of all at once.
_PROBE_BATCH_SIZE = 50


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS options_contract_cache (
                symbol TEXT PRIMARY KEY,
                underlying TEXT NOT NULL,
                expiration TEXT NOT NULL,
                right TEXT NOT NULL,
                strike REAL NOT NULL,
                has_data INTEGER NOT NULL,
                bar_count INTEGER NOT NULL DEFAULT 0,
                checked_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_options_contract_cache_underlying "
            "ON options_contract_cache(underlying, expiration)"
        )
        conn.commit()
    finally:
        conn.close()


_init_schema()


def third_friday(year: int, month: int) -> date:
    """Standard monthly options expiration date (3rd Friday of the month)."""
    d = date(year, month, 1)
    first_friday_offset = (4 - d.weekday()) % 7
    first_friday = d + timedelta(days=first_friday_offset)
    return first_friday + timedelta(days=14)


def monthly_expirations(start: date, end: date) -> List[date]:
    """Every 3rd-Friday monthly expiration in ``[start, end]``, plus one
    month of slack on each side so a position opened near a window edge can
    still find its real expiration just outside the nominal range."""
    year, month = start.year, start.month - 1
    if month <= 0:
        month += 12
        year -= 1
    expirations: List[date] = []
    while True:
        exp = third_friday(year, month)
        if exp > end + timedelta(days=31):
            break
        if exp >= start - timedelta(days=31):
            expirations.append(exp)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return expirations


def strike_grid(reference_price: float, *, levels: int = 5) -> List[float]:
    """Strikes spaced around ``reference_price`` at the increment real listed
    strikes typically use for that price tier ($0.50/$1 for lower-priced
    names, up to $10 for higher-priced ones)."""
    if reference_price <= 0:
        return []
    if reference_price < 25:
        step = 0.5
    elif reference_price < 100:
        step = 1.0
    elif reference_price < 250:
        step = 5.0
    else:
        step = 10.0
    center = round(reference_price / step) * step
    strikes = {round(center + i * step, 2) for i in range(-levels, levels + 1)}
    return sorted(s for s in strikes if s > 0)


def _fetch_underlying_daily_closes(underlying: str, start: date, end: date) -> Dict[date, float]:
    """A small, self-contained equity daily-close fetch -- deliberately not
    reaching into domain.leaderboard.catalog's private _fetch_daily_bars
    across a domain boundary just for one field (close price)."""
    import os

    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        return {}

    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        return {}
    client = StockHistoricalDataClient(api_key, secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=[underlying], timeframe=TimeFrame.Day, start=start, end=end,
    )
    bars = client.get_stock_bars(request)
    data = getattr(bars, "data", {}) or {}
    closes: Dict[date, float] = {}
    for bar in data.get(underlying.upper(), []):
        ts = bar.timestamp
        day = ts.date() if hasattr(ts, "date") else ts
        closes[day] = float(bar.close)
    return closes


@dataclass
class CandidateContract:
    symbol: str
    underlying: str
    expiration: date
    right: str
    strike: float
    bar_count: int


def _cache_get(symbol: str) -> Optional[Dict]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM options_contract_cache WHERE symbol = ?", (symbol,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _cache_put(
    symbol: str, underlying: str, expiration: date, right: str, strike: float,
    has_data: bool, bar_count: int,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO options_contract_cache
                (symbol, underlying, expiration, right, strike, has_data, bar_count, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                has_data = excluded.has_data,
                bar_count = excluded.bar_count,
                checked_at = excluded.checked_at
            """,
            (
                symbol, underlying, expiration.isoformat(), right, strike,
                int(has_data), bar_count, datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _closest_close(closes: Dict[date, float], target: date) -> Optional[float]:
    if not closes:
        return None
    best = min(closes.keys(), key=lambda d: abs((d - target).days))
    return closes[best]


def find_candidate_contracts(
    underlying: str, start: date, end: date, *, rights: Tuple[str, ...] = ("C", "P"),
) -> List[CandidateContract]:
    """Synthesize + probe candidate OCC symbols for ``underlying`` over
    ``[start, end]``, returning only the ones confirmed (via cache or a
    fresh probe) to have real historical daily bars.

    Returns an empty list (rather than raising) when market data is
    unavailable -- callers (the backtester) treat "no candidates" as "this
    underlying/window can't be backtested," not a hard error, since a
    missing API key must not crash the whole run."""
    underlying = underlying.upper()
    expirations = monthly_expirations(start, end)
    closes = _fetch_underlying_daily_closes(underlying, start, end)
    if not closes:
        return []

    results: List[CandidateContract] = []
    # (symbol, expiration, right, strike) for everything not yet cached.
    to_probe: List[Tuple[str, date, str, float]] = []
    for expiration in expirations:
        reference_price = _closest_close(closes, expiration - timedelta(days=30))
        if not reference_price:
            continue
        for strike in strike_grid(reference_price):
            for right in rights:
                symbol = synthesize_occ_symbol(underlying, expiration, right, strike)
                cached = _cache_get(symbol)
                if cached is not None:
                    if cached["has_data"]:
                        results.append(CandidateContract(
                            symbol=symbol, underlying=underlying, expiration=expiration,
                            right=right, strike=strike, bar_count=cached["bar_count"],
                        ))
                    continue
                to_probe.append((symbol, expiration, right, strike))

    for batch_start in range(0, len(to_probe), _PROBE_BATCH_SIZE):
        batch = to_probe[batch_start:batch_start + _PROBE_BATCH_SIZE]
        symbols = [item[0] for item in batch]
        try:
            bars_by_symbol = get_option_bars(symbols, start, end)
        except MarketDataUnavailableError:
            # Leave this batch unprobed/uncached rather than caching a false
            # "no data" -- a transient outage must not poison the cache with
            # a permanent-looking negative result.
            continue
        for symbol, expiration, right, strike in batch:
            bars = bars_by_symbol.get(symbol)
            bar_count = len(bars) if bars is not None else 0
            has_data = bar_count > 0
            _cache_put(symbol, underlying, expiration, right, strike, has_data, bar_count)
            if has_data:
                results.append(CandidateContract(
                    symbol=symbol, underlying=underlying, expiration=expiration,
                    right=right, strike=strike, bar_count=bar_count,
                ))

    return results
