"""SQLite store for Prediction strategies -- one unified table and state
machine for all three creation paths the user asked for (Manual, My Agents,
Testing/Upload), unlike every other asset class which has three separate
tables/flows. See domain/prediction/engine.py's module docstring for why:
Prediction has no instant historical backtest at all, so there's no
"Testing queue vs Manual vs Catalog" split to preserve -- everything is the
same 5-real-day forward paper-test, just started from a different source.

Status pipeline, always advanced by the daily scheduler tick (never by an
API request directly, so a page reload can never desync the state machine):
    waiting (day_count 0..4, ticked once per real day)
    -> ready (day_count reaches 5, results become visible)
    -> added | deleted (a human's decision once ready)
A code/prompt that fails validation never reaches ``waiting`` at all --
``rejected`` is a terminal status set at submission time, not by a tick.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dashboard.backend.database import DB_PATH

TERMINAL_STATUSES = {"rejected", "added", "deleted"}
WAITING_DAYS_REQUIRED = 5
DEFAULT_INITIAL_CAPITAL = 1000.0


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(Path(DB_PATH)), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return conn


def init_schema() -> None:
    conn = _get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prediction_strategies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            source_type TEXT NOT NULL,
            code TEXT,
            strategy_prompt TEXT,
            model TEXT,
            agent_id TEXT,
            status TEXT NOT NULL DEFAULT 'waiting',
            day_count INTEGER NOT NULL DEFAULT 0,
            last_ticked_date TEXT,
            initial_capital REAL NOT NULL DEFAULT 1000.0,
            cash REAL NOT NULL DEFAULT 1000.0,
            equity_curve_json TEXT,
            positions_json TEXT,
            total_fees_paid REAL NOT NULL DEFAULT 0.0,
            review_notes TEXT,
            error TEXT,
            user_id INTEGER,
            submitted_at TEXT NOT NULL,
            ready_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["equity_curve"] = json.loads(data.pop("equity_curve_json") or "[]")
    data["positions"] = json.loads(data.pop("positions_json") or "[]")
    data.pop("code", None)  # never ship raw source in list/status payloads
    return data


def _row_to_dict_with_code(row: sqlite3.Row) -> Dict[str, Any]:
    """Internal use only (the scheduler tick) -- includes the raw source."""
    data = dict(row)
    data["equity_curve"] = json.loads(data.pop("equity_curve_json") or "[]")
    data["positions"] = json.loads(data.pop("positions_json") or "[]")
    return data


def create(
    *,
    name: str,
    description: str,
    source_type: str,
    code: Optional[str] = None,
    strategy_prompt: Optional[str] = None,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    review_notes: Optional[str] = None,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    row_id = uuid.uuid4().hex
    now = _utcnow_iso()
    conn = _get_connection()
    conn.execute(
        """
        INSERT INTO prediction_strategies
            (id, name, description, source_type, code, strategy_prompt, model, agent_id,
             status, day_count, initial_capital, cash, equity_curve_json, positions_json,
             review_notes, user_id, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'waiting', 0, ?, ?, '[]', '[]', ?, ?, ?)
        """,
        (
            row_id, name, description or "", source_type, code, strategy_prompt, model, agent_id,
            initial_capital, initial_capital, review_notes, user_id, now,
        ),
    )
    conn.commit()
    conn.close()
    return get(row_id)


def create_rejected(
    *, name: str, description: str, source_type: str, code: Optional[str], error: str,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """A submission that failed AST validation -- recorded so it shows up in
    History, never enters the waiting list at all (see module docstring)."""
    row_id = uuid.uuid4().hex
    now = _utcnow_iso()
    conn = _get_connection()
    conn.execute(
        """
        INSERT INTO prediction_strategies
            (id, name, description, source_type, code, status, day_count,
             initial_capital, cash, equity_curve_json, positions_json, error, user_id, submitted_at)
        VALUES (?, ?, ?, ?, ?, 'rejected', 0, ?, ?, '[]', '[]', ?, ?, ?)
        """,
        (row_id, name, description or "", source_type, code, DEFAULT_INITIAL_CAPITAL, DEFAULT_INITIAL_CAPITAL,
         error, user_id, now),
    )
    conn.commit()
    conn.close()
    return get(row_id)


def get(row_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_connection()
    row = conn.execute("SELECT * FROM prediction_strategies WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def get_with_code(row_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_connection()
    row = conn.execute("SELECT * FROM prediction_strategies WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    return _row_to_dict_with_code(row) if row else None


def list_all(*, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = _get_connection()
    if user_id is not None:
        rows = conn.execute(
            "SELECT * FROM prediction_strategies WHERE user_id = ? ORDER BY submitted_at DESC", (user_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM prediction_strategies ORDER BY submitted_at DESC").fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def list_due_for_tick(as_of: str) -> List[Dict[str, Any]]:
    """Every ``waiting`` OR ``added`` strategy not yet ticked today -- the
    scheduler's worklist for this tick cycle. ``waiting`` is the 5-day
    probation; ``added`` is a strategy a human has kept, which keeps trading
    forward indefinitely on the same daily tick -- the 5-day wait gates when
    results first become visible, not how long the strategy trades.
    ``last_ticked_date`` is NULL on a same-day submission, so it's always
    due on its first eligible day."""
    conn = _get_connection()
    rows = conn.execute(
        """
        SELECT * FROM prediction_strategies
        WHERE status IN ('waiting', 'added') AND (last_ticked_date IS NULL OR last_ticked_date != ?)
        ORDER BY submitted_at ASC
        """,
        (as_of,),
    ).fetchall()
    conn.close()
    return [_row_to_dict_with_code(r) for r in rows]


def record_tick(
    row_id: str,
    *,
    as_of: str,
    cash: float,
    positions: List[Dict[str, Any]],
    equity_point: Dict[str, Any],
    fees_paid_today: float,
) -> Dict[str, Any]:
    """Advance one strategy by exactly one day: append to its curve,
    increment day_count, and -- only once day_count reaches
    WAITING_DAYS_REQUIRED -- flip it to ``ready``."""
    conn = _get_connection()
    row = conn.execute("SELECT * FROM prediction_strategies WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"no prediction strategy {row_id!r}")

    curve = json.loads(row["equity_curve_json"] or "[]")
    curve.append(equity_point)
    new_day_count = int(row["day_count"]) + 1
    if row["status"] == "added":
        # Already kept by a human -- keeps trading forward on the same tick,
        # day_count keeps climbing as a track-record length, but the status
        # itself must never regress back to "ready" (a re-review state that
        # only makes sense before the 5-day probation has been decided on).
        new_status = "added"
    else:
        new_status = "ready" if new_day_count >= WAITING_DAYS_REQUIRED else "waiting"
    ready_at = _utcnow_iso() if new_status == "ready" and row["status"] != "ready" else row["ready_at"]

    conn.execute(
        """
        UPDATE prediction_strategies
        SET day_count = ?, last_ticked_date = ?, status = ?, cash = ?,
            positions_json = ?, equity_curve_json = ?, total_fees_paid = total_fees_paid + ?,
            ready_at = ?
        WHERE id = ?
        """,
        (new_day_count, as_of, new_status, cash, json.dumps(positions), json.dumps(curve),
         fees_paid_today, ready_at, row_id),
    )
    conn.commit()
    conn.close()
    return get(row_id)


def mark_error(row_id: str, *, error: str) -> None:
    conn = _get_connection()
    conn.execute(
        "UPDATE prediction_strategies SET status = 'error', error = ? WHERE id = ?",
        (error[:2000], row_id),
    )
    conn.commit()
    conn.close()


def mark_added(row_id: str) -> None:
    conn = _get_connection()
    conn.execute("UPDATE prediction_strategies SET status = 'added' WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()


def mark_deleted(row_id: str) -> None:
    conn = _get_connection()
    conn.execute("UPDATE prediction_strategies SET status = 'deleted' WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()


def delete_permanently(row_id: str) -> None:
    conn = _get_connection()
    conn.execute("DELETE FROM prediction_strategies WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()
