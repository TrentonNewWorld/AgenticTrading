"""SQLite queue for the Testing page: one row per submitted strategy.

Statuses form a strict pipeline, always advanced by the worker thread (never
by an API request directly, so a page reload can never desync the state
machine): queued -> scanning -> (rejected, stops here) -> backtesting ->
(error, stops here) -> ready -> (added | deleted, stops here).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dashboard.backend.database import DB_PATH

TERMINAL_STATUSES = {"rejected", "error", "added", "deleted"}


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
        CREATE TABLE IF NOT EXISTS strategy_test_queue (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            code TEXT NOT NULL,
            source_filename TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            submitted_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            scan_verdict TEXT,
            scan_notes TEXT,
            scan_is_strategy INTEGER,
            error TEXT,
            result_json TEXT,
            asset_class TEXT NOT NULL DEFAULT 'stocks',
            user_id INTEGER
        )
        """
    )
    conn.commit()
    conn.close()
    _migrate_schema()


def _migrate_schema() -> None:
    """CREATE TABLE IF NOT EXISTS above no-ops once the table already exists,
    so an on-disk DB created before asset_class/user_id existed (this repo's
    own local dev DB included) never gains them without an explicit ALTER
    TABLE."""
    conn = _get_connection()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(strategy_test_queue)").fetchall()}
        if "asset_class" not in columns:
            conn.execute(
                "ALTER TABLE strategy_test_queue ADD COLUMN asset_class TEXT DEFAULT 'stocks'"
            )
            conn.execute(
                "UPDATE strategy_test_queue SET asset_class = 'stocks' WHERE asset_class IS NULL"
            )
        if "user_id" not in columns:
            # Nullable, no backfill needed: NULL means "submitted signed out
            # (or before this column existed)" -- the LLM review then falls
            # back to the server's own key exactly as it always has.
            conn.execute("ALTER TABLE strategy_test_queue ADD COLUMN user_id INTEGER")
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    if data.get("result_json"):
        try:
            data["result"] = json.loads(data["result_json"])
        except (TypeError, ValueError):
            data["result"] = None
    else:
        data["result"] = None
    data.pop("result_json", None)
    data.pop("code", None)  # never ship raw source in list/status payloads
    data["scan_is_strategy"] = bool(data.get("scan_is_strategy"))
    return data


def enqueue(
    *, name: str, description: str, code: str, source_filename: Optional[str] = None,
    asset_class: str = "stocks", user_id: Optional[int] = None,
) -> Dict[str, Any]:
    row_id = uuid.uuid4().hex
    now = _utcnow_iso()
    conn = _get_connection()
    conn.execute(
        """
        INSERT INTO strategy_test_queue
            (id, name, description, code, source_filename, status, submitted_at, asset_class, user_id)
        VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)
        """,
        (row_id, name, description or "", code, source_filename, now, asset_class, user_id),
    )
    conn.commit()
    conn.close()
    return get(row_id)


def get(row_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_connection()
    row = conn.execute("SELECT * FROM strategy_test_queue WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def get_with_code(row_id: str) -> Optional[Dict[str, Any]]:
    """Internal use only (worker, code export) -- includes the raw source."""
    conn = _get_connection()
    row = conn.execute("SELECT * FROM strategy_test_queue WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    if data.get("result_json"):
        try:
            data["result"] = json.loads(data["result_json"])
        except (TypeError, ValueError):
            data["result"] = None
    return data


def list_all(asset_class: str = "stocks") -> List[Dict[str, Any]]:
    """Defaults to 'stocks' -- every existing caller (the Testing page's own
    API) passes nothing and must keep seeing exactly what it saw before
    Options started sharing this queue table, not a mixed list."""
    conn = _get_connection()
    rows = conn.execute(
        "SELECT * FROM strategy_test_queue WHERE asset_class = ? ORDER BY submitted_at DESC",
        (asset_class,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def next_queued() -> Optional[Dict[str, Any]]:
    """Oldest still-queued row, for the worker to pick up next (FIFO)."""
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM strategy_test_queue WHERE status = 'queued' ORDER BY submitted_at ASC LIMIT 1"
    ).fetchone()
    conn.close()
    return get_with_code(row["id"]) if row else None


def set_status(row_id: str, status: str, **fields: Any) -> None:
    conn = _get_connection()
    columns = ["status"]
    values: List[Any] = [status]
    for key, value in fields.items():
        columns.append(key)
        values.append(value)
    set_clause = ", ".join(f"{c} = ?" for c in columns)
    values.append(row_id)
    conn.execute(f"UPDATE strategy_test_queue SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def mark_scanning(row_id: str) -> None:
    set_status(row_id, "scanning", started_at=_utcnow_iso())


def mark_rejected(row_id: str, *, scan_verdict: str, scan_notes: str, scan_is_strategy: bool) -> None:
    set_status(
        row_id, "rejected", finished_at=_utcnow_iso(),
        scan_verdict=scan_verdict, scan_notes=scan_notes, scan_is_strategy=int(scan_is_strategy),
    )


def mark_backtesting(row_id: str, *, scan_verdict: str, scan_notes: str, scan_is_strategy: bool) -> None:
    set_status(
        row_id, "backtesting",
        scan_verdict=scan_verdict, scan_notes=scan_notes, scan_is_strategy=int(scan_is_strategy),
    )


def mark_error(row_id: str, *, error: str) -> None:
    set_status(row_id, "error", finished_at=_utcnow_iso(), error=error[:2000])


def mark_ready(row_id: str, *, result: Dict[str, Any]) -> None:
    set_status(row_id, "ready", finished_at=_utcnow_iso(), result_json=json.dumps(result))


def mark_added(row_id: str) -> None:
    set_status(row_id, "added")


def mark_deleted(row_id: str) -> None:
    set_status(row_id, "deleted")


def delete_permanently(row_id: str) -> None:
    conn = _get_connection()
    conn.execute("DELETE FROM strategy_test_queue WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()
