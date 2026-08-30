"""Real Trading ledger: tracks each strategy's profit in actual paper/live
trading, year to year, separately from the Competition Leaderboard's backtest
simulation.

Why a notional sub-ledger instead of reading the broker account directly:
Alpaca account state (equity, positions) is per-ACCOUNT, and one paper/live
account can run many strategies at once. There is no way to tell, from the
account's real positions alone, which shares belong to which strategy's
decisions -- two strategies that both want AAPL would have their AAPL
holdings conflated into one number. So each strategy gets its own notional
sub-portfolio here: an isolated set of "holdings" marked to real market
prices, sized against that strategy's own allocated capital (set on the
Strategy Catalog page), and rebalanced to the strategy's own target weights
every time it actually runs in paper or live mode. This is the same
share-counting math the real broker path uses (compute_rebalance_orders in
_alpaca_strategy_shared.py) -- just applied to a bookkeeping entry instead of
a real order -- so a strategy's number here reflects exactly what its own
decide() calls would have done with its own money, uncontaminated by
whatever else is happening in the shared account.

Persisted in the same SQLite file as everything else (DATABASE_PATH), in two
tables this module owns outright: `strategy_allocations` (the user-settable
starting capital per strategy) and `real_trading_ledger` (each strategy's
current notional holdings + its equity-point history for the chart).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dashboard.backend.database import DB_PATH

#: The tracking window this ledger reports over. Not enforced against the
#: real calendar (a real run in December 2026 still gets recorded), but
#: get_real_trading_leaderboard() clips its returned point series to this
#: window so a stray pre-2026 test run cannot appear on the chart.
TRACKING_START = "2026-01-01"
TRACKING_END = "2027-01-01"

DEFAULT_ALLOCATION = 1000.0


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema() -> None:
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_allocations (
                strategy_key TEXT PRIMARY KEY,
                allocated_capital REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # per_stock_amount: added after the table shipped, so an existing row
        # from before this column existed needs the same ALTER-guarded
        # migration pattern database.py uses elsewhere in this repo.
        existing_columns = {
            row["name"] for row in cursor.execute("PRAGMA table_info(strategy_allocations)")
        }
        if "per_stock_amount" not in existing_columns:
            cursor.execute("ALTER TABLE strategy_allocations ADD COLUMN per_stock_amount REAL")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS real_trading_holdings (
                strategy_key TEXT PRIMARY KEY,
                cash REAL NOT NULL,
                shares_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS real_trading_equity_points (
                strategy_key TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                equity REAL NOT NULL,
                mode TEXT NOT NULL,
                PRIMARY KEY (strategy_key, timestamp)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


_init_schema()


def get_allocation(strategy_key: str) -> float:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT allocated_capital FROM strategy_allocations WHERE strategy_key = ?",
            (strategy_key,),
        ).fetchone()
        return float(row["allocated_capital"]) if row else DEFAULT_ALLOCATION
    finally:
        conn.close()


def get_all_allocations() -> Dict[str, float]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT strategy_key, allocated_capital FROM strategy_allocations").fetchall()
        return {r["strategy_key"]: float(r["allocated_capital"]) for r in rows}
    finally:
        conn.close()


def set_allocation(strategy_key: str, amount: float) -> None:
    if not (amount > 0):
        raise ValueError("allocated capital must be a positive number")
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO strategy_allocations (strategy_key, allocated_capital, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                allocated_capital = excluded.allocated_capital,
                updated_at = excluded.updated_at
            """,
            (strategy_key, float(amount), now),
        )
        conn.commit()
    finally:
        conn.close()


def get_per_stock_amount(strategy_key: str) -> Optional[float]:
    """None means "no fixed amount" -- the strategy's own target weights size
    each position proportionally against its allocated capital instead (see
    _effective_target_weights in alpaca_paper_service.py/alpaca_live_service.py)."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT per_stock_amount FROM strategy_allocations WHERE strategy_key = ?",
            (strategy_key,),
        ).fetchone()
        return float(row["per_stock_amount"]) if row and row["per_stock_amount"] is not None else None
    finally:
        conn.close()


def set_per_stock_amount(strategy_key: str, amount: Optional[float]) -> None:
    """`amount=None` clears the fixed-dollar-per-stock override, reverting to
    weight-proportional sizing against the strategy's allocated capital."""
    if amount is not None and not (amount > 0):
        raise ValueError("per-stock amount must be a positive number")
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO strategy_allocations (strategy_key, allocated_capital, per_stock_amount, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                per_stock_amount = excluded.per_stock_amount,
                updated_at = excluded.updated_at
            """,
            (strategy_key, DEFAULT_ALLOCATION, amount, now),
        )
        conn.commit()
    finally:
        conn.close()


def _load_holdings(conn: sqlite3.Connection, strategy_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT cash, shares_json FROM real_trading_holdings WHERE strategy_key = ?",
        (strategy_key,),
    ).fetchone()
    if not row:
        return None
    return {"cash": float(row["cash"]), "shares": json.loads(row["shares_json"])}


def record_real_trading_snapshot(
    strategy_key: str,
    mode: str,
    target_weights: Optional[Dict[str, float]],
    prices: Dict[str, float],
) -> float:
    """Mark this strategy's notional sub-portfolio to current real prices,
    rebalance it to `target_weights` (skipped if None -- the strategy decided
    to make no trades this cycle, same tri-state contract `decide()` uses
    elsewhere), and record one equity point. Returns the resulting equity.

    Called from the paper/live execution path every time a user actually
    runs a strategy -- this is deliberately not on a timer. A strategy with
    no real trading activity has no data here, by design.
    """
    conn = _connect()
    try:
        existing = _load_holdings(conn, strategy_key)
        if existing is None:
            allocation_row = conn.execute(
                "SELECT allocated_capital FROM strategy_allocations WHERE strategy_key = ?",
                (strategy_key,),
            ).fetchone()
            starting_cash = float(allocation_row["allocated_capital"]) if allocation_row else DEFAULT_ALLOCATION
            shares: Dict[str, float] = {}
            cash = starting_cash
        else:
            shares = dict(existing["shares"])
            cash = existing["cash"]

        # Mark existing notional holdings to current real prices.
        portfolio_value = cash + sum(
            qty * prices[sym] for sym, qty in shares.items() if sym in prices and prices[sym]
        )

        if target_weights:
            new_shares: Dict[str, float] = {}
            new_cash = portfolio_value
            for sym, weight in target_weights.items():
                price = prices.get(sym)
                if not price or price <= 0 or weight <= 0:
                    continue
                qty = (weight * portfolio_value) / price
                new_shares[sym] = qty
                new_cash -= qty * price
            shares = new_shares
            cash = new_cash

        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO real_trading_holdings (strategy_key, cash, shares_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                cash = excluded.cash,
                shares_json = excluded.shares_json,
                updated_at = excluded.updated_at
            """,
            (strategy_key, cash, json.dumps(shares), now),
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO real_trading_equity_points (strategy_key, timestamp, equity, mode)
            VALUES (?, ?, ?, ?)
            """,
            (strategy_key, now, portfolio_value, mode),
        )
        conn.commit()
        return portfolio_value
    finally:
        conn.close()


def get_real_trading_leaderboard() -> Dict[str, Any]:
    """Every strategy that has at least one real trading snapshot, with its
    allocation, current value, profit, and point history clipped to the
    TRACKING_START..TRACKING_END window."""
    conn = _connect()
    try:
        allocations = {
            r["strategy_key"]: float(r["allocated_capital"])
            for r in conn.execute("SELECT strategy_key, allocated_capital FROM strategy_allocations")
        }
        keys = [
            r["strategy_key"]
            for r in conn.execute("SELECT DISTINCT strategy_key FROM real_trading_equity_points")
        ]

        entries = []
        for key in keys:
            points = conn.execute(
                """
                SELECT timestamp, equity FROM real_trading_equity_points
                WHERE strategy_key = ? AND timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC
                """,
                (key, TRACKING_START, TRACKING_END),
            ).fetchall()
            if not points:
                continue
            allocation = allocations.get(key, DEFAULT_ALLOCATION)
            current = float(points[-1]["equity"])
            profit = current - allocation
            entries.append({
                "key": key,
                "allocated_capital": allocation,
                "current_value": round(current, 2),
                "profit": round(profit, 2),
                "return_pct": round((profit / allocation) * 100, 2) if allocation else 0.0,
                "points": [{"t": p["timestamp"], "equity": round(float(p["equity"]), 2)} for p in points],
            })

        entries.sort(key=lambda e: e["return_pct"], reverse=True)
        return {
            "window": {"start": TRACKING_START, "end": TRACKING_END},
            "entries": entries,
        }
    finally:
        conn.close()
