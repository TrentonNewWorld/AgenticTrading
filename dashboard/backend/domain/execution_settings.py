"""Runtime-toggleable overrides for the *_EXECUTE kill switches this repo
otherwise only reads from the environment (ALPACA_LIVE_EXECUTE and friends).

Env vars are fine for a one-time deploy-time decision, but they can't be
flipped from the running app -- editing .env does nothing to an already-
running process (it's read once, at import, via python-dotenv), and this
app has no privileged shell access from the UI to restart itself. This
table is the missing piece: a DB row `execute_enabled()` checks on every
call, read fresh (never cached) the same way the env var itself always was,
so arming/disarming takes effect on the very next tick, not the next
deploy.

Precedence: once a row exists for a key, it wins outright -- the env var is
only the *initial* value, for a fresh install or a hosted deploy that never
touches the UI toggle. There is no "env var wins after all" fallback once a
person has explicitly flipped the switch; that would silently undo a
person's last action on the next server restart, which is worse than either
value being wrong on its own.

Scope: currently just ``alpaca_live_execute``, the flag Strategy Catalog's
Run in Live and the catalog scheduler's live tick both gate on. Deliberately
NOT a generic settings table for every *_EXECUTE flag in this repo -- add a
key here only when a real UI control needs it, matching this repo's existing
one-small-table-per-feature convention rather than building a speculative
generic store nothing else uses yet.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from dashboard.backend.database import DB_PATH

ALPACA_LIVE_EXECUTE_KEY = "alpaca_live_execute"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_settings (
                key TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by_user_id INTEGER
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


_init_schema()


def get_override(key: str) -> Optional[bool]:
    """``None`` when nobody has ever touched this via the UI -- the caller
    falls back to its own env var default in that case."""
    conn = _connect()
    try:
        row = conn.execute("SELECT enabled FROM execution_settings WHERE key = ?", (key,)).fetchone()
        return bool(row["enabled"]) if row is not None else None
    finally:
        conn.close()


def set_override(key: str, enabled: bool, *, user_id: Optional[int] = None) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO execution_settings (key, enabled, updated_at, updated_by_user_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                enabled = excluded.enabled, updated_at = excluded.updated_at, updated_by_user_id = excluded.updated_by_user_id
            """,
            (key, int(enabled), now, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return enabled


def get_status(key: str) -> dict:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT enabled, updated_at, updated_by_user_id FROM execution_settings WHERE key = ?", (key,),
        ).fetchone()
        if row is None:
            return {"enabled": None, "updated_at": None, "updated_by_user_id": None}
        return {"enabled": bool(row["enabled"]), "updated_at": row["updated_at"], "updated_by_user_id": row["updated_by_user_id"]}
    finally:
        conn.close()
