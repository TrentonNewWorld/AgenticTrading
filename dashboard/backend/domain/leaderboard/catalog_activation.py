"""Persistence for Strategy Catalog "keep running until I turn it off"
activation state -- what domain/leaderboard/catalog_scheduler.py's daily
daemon reads to know which strategies to run, and what the paper/live
Activate/Deactivate buttons on the catalog page write.

Before this module, Run in Paper/Run in Live were one-shot buttons: click,
one decision cycle runs right now, nothing persists. A user who wanted a
strategy to actually trade every day had no way to ask for that -- the
button looked like it should mean "start trading this," not "trade this
once," and there was no daily scheduler to begin with. This is the missing
state; catalog_scheduler.py is the missing loop.

Paper and live are independent activations of the same strategy_key (a
strategy can run in one, both, or neither) -- mirrors the catalog page's
existing two separate buttons rather than inventing a combined toggle.
``user_id`` is stored per activation (not just per request) because the
scheduler runs unattended with no live signed-in caller: it's whoever
activated the strategy whose Connections-saved Alpaca key
(alpaca_paper_service/alpaca_live_service's existing ``user_id`` param)
should keep being used for every future scheduled tick, the same reasoning
domain/prediction/engine.py's per-row user_id already established for its
own unattended scheduler.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dashboard.backend.database import DB_PATH

Mode = str  # "paper" | "live"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_activations (
                strategy_key TEXT NOT NULL,
                mode TEXT NOT NULL,
                activated INTEGER NOT NULL DEFAULT 0,
                user_id INTEGER,
                activated_at TEXT,
                last_run_at TEXT,
                last_run_trading_date TEXT,
                last_run_status TEXT,
                PRIMARY KEY (strategy_key, mode)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


_init_schema()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "strategy_key": row["strategy_key"],
        "mode": row["mode"],
        "activated": bool(row["activated"]),
        "user_id": row["user_id"],
        "activated_at": row["activated_at"],
        "last_run_at": row["last_run_at"],
        "last_run_trading_date": row["last_run_trading_date"],
        "last_run_status": row["last_run_status"],
    }


def get(strategy_key: str, mode: Mode) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM catalog_activations WHERE strategy_key = ? AND mode = ?",
            (strategy_key, mode),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_all() -> List[Dict[str, Any]]:
    """Every activation row (activated or not) -- the catalog list route
    merges this in so each card can show its current state."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM catalog_activations").fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def list_activated(mode: Mode) -> List[Dict[str, Any]]:
    """Rows the scheduler should consider ticking for this mode."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM catalog_activations WHERE mode = ? AND activated = 1", (mode,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def activate(strategy_key: str, mode: Mode, user_id: Optional[int]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO catalog_activations (strategy_key, mode, activated, user_id, activated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(strategy_key, mode) DO UPDATE SET
                activated = 1, user_id = excluded.user_id, activated_at = excluded.activated_at
            """,
            (strategy_key, mode, user_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get(strategy_key, mode)


def deactivate(strategy_key: str, mode: Mode) -> Dict[str, Any]:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE catalog_activations SET activated = 0 WHERE strategy_key = ? AND mode = ?",
            (strategy_key, mode),
        )
        conn.commit()
    finally:
        conn.close()
    existing = get(strategy_key, mode)
    return existing or {
        "strategy_key": strategy_key, "mode": mode, "activated": False, "user_id": None,
        "activated_at": None, "last_run_at": None, "last_run_trading_date": None, "last_run_status": None,
    }


def record_tick(strategy_key: str, mode: Mode, trading_date: str, status: str) -> None:
    """Called by the scheduler after each attempted run (success or error)
    -- ``last_run_trading_date`` is what makes a tick idempotent within a
    day: the scheduler's poll interval is much shorter than one trading
    day, and this is the only reason it isn't re-running every poll."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE catalog_activations
            SET last_run_at = ?, last_run_trading_date = ?, last_run_status = ?
            WHERE strategy_key = ? AND mode = ?
            """,
            (now, trading_date, status, strategy_key, mode),
        )
        conn.commit()
    finally:
        conn.close()
