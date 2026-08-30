"""Manual 10's own tables in the shared SQLite file (DATABASE_PATH) -- same
connect-per-call pattern as real_trading.py/strategy_overrides.py elsewhere in
domain/leaderboard, applied here since this feature has the same shape
(a few small tables, no need for an ORM or a long-lived connection).

Everything below is scoped by `strategy_key`: the "Manual" page runs multiple
independently-selectable, independently-activatable strategies side by side
(the built-in Top 10 opening-range screener, plus any user-uploaded ones),
each with its own daily phase, candidates, positions, and price history --
never sharing state across strategy_key values even for the same symbol on
the same day.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dashboard.backend.database import DB_PATH

TOP_10_STRATEGY_KEY = "top_10"


def _now() -> datetime:
    """Indirection point so tests can drive this module with a fake market
    clock instead of real wall-clock time -- in production these are the same
    instant, since the engine only ever runs against the live market clock,
    but a test walking a simulated trading day needs entry/exit timestamps to
    move with the fake clock, not with however long the test takes to run."""
    return datetime.now(timezone.utc)


DEFAULT_SETTINGS: Dict[str, Any] = {
    "screener_window_minutes": 10,
    "top_n": 10,
    "buy_in_per_stock": 10.0,
    "price_min": 1.0,
    "price_max": 99.0,
    "promotion_window_minutes": 6,
    "close_out_minutes_before_close": 30,
    "straggler_check_minutes_before_close": 15,
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema() -> None:
    conn = _connect()
    try:
        # Top 10's own tunable settings (screener window, buy-in, price band,
        # etc). Singleton today because it's the only built-in strategy with
        # this settings shape; an uploaded strategy carries its own
        # interval_minutes directly on manual10_strategies instead.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual10_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                screener_window_minutes INTEGER NOT NULL,
                top_n INTEGER NOT NULL,
                buy_in_per_stock REAL NOT NULL,
                price_min REAL NOT NULL,
                price_max REAL NOT NULL,
                promotion_window_minutes INTEGER NOT NULL,
                close_out_minutes_before_close INTEGER NOT NULL,
                straggler_check_minutes_before_close INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual10_strategies (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                description TEXT,
                code TEXT,
                interval_minutes INTEGER,
                review_status TEXT NOT NULL DEFAULT 'approved',
                review_notes TEXT,
                created_at TEXT NOT NULL,
                asset_class TEXT NOT NULL DEFAULT 'stocks'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual10_activations (
                trading_date TEXT NOT NULL,
                strategy_key TEXT NOT NULL,
                selected INTEGER NOT NULL DEFAULT 0,
                activated INTEGER NOT NULL DEFAULT 0,
                activated_at TEXT,
                asset_class TEXT NOT NULL DEFAULT 'stocks',
                PRIMARY KEY (trading_date, strategy_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual10_days (
                trading_date TEXT NOT NULL,
                strategy_key TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT 'pending',
                wallet_reset_amount REAL,
                screener_started_at TEXT,
                screener_completed_at TEXT,
                closed_at TEXT,
                realized_pnl REAL,
                result TEXT,
                created_at TEXT NOT NULL,
                asset_class TEXT NOT NULL DEFAULT 'stocks',
                PRIMARY KEY (trading_date, strategy_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual10_candidates (
                trading_date TEXT NOT NULL,
                strategy_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                open_price REAL,
                latest_price REAL,
                change_pct REAL,
                rank INTEGER,
                picked INTEGER NOT NULL DEFAULT 0,
                asset_class TEXT NOT NULL DEFAULT 'stocks',
                PRIMARY KEY (trading_date, strategy_key, symbol)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual10_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trading_date TEXT NOT NULL,
                strategy_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                bucket TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                shares REAL NOT NULL,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                exit_price REAL,
                exit_time TEXT,
                close_reason TEXT,
                real_order_id TEXT,
                promoted_from_paper INTEGER NOT NULL DEFAULT 0,
                promotion_checked INTEGER NOT NULL DEFAULT 0,
                promotion_note TEXT,
                updated_at TEXT NOT NULL,
                asset_class TEXT NOT NULL DEFAULT 'stocks',
                underlying_symbol TEXT,
                strike_price REAL,
                expiration_date TEXT,
                option_right TEXT,
                contract_multiplier INTEGER,
                leg_group_id TEXT,
                leg_role TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual10_price_snapshots (
                strategy_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                ts TEXT NOT NULL,
                price REAL NOT NULL,
                PRIMARY KEY (strategy_key, symbol, trading_date, ts)
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO manual10_strategies (key, name, kind, description, review_status, created_at)
            VALUES (?, 'Top 10', 'builtin',
                    'Scans the first N minutes of market open for the biggest $1-$99 gainers, buys the top picks in paper, and promotes any still up after a short window to real money.',
                    'approved', ?)
            """,
            (TOP_10_STRATEGY_KEY, _now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_schema() -> None:
    """Add columns to tables that predate them -- CREATE TABLE IF NOT EXISTS
    above no-ops once a table already exists, so an on-disk DB created before
    this migration (this repo's own local dev DB included) never gains these
    columns without an explicit ALTER TABLE. Mirrors the pattern in
    dashboard/backend/database.py's _migrate_schema()."""
    conn = _connect()
    try:
        # asset_class: added to every manual10 table so Options (and later
        # Futures/Forex/Crypto) can share these tables with Stocks without
        # their rows colliding. Defaulted to 'stocks' rather than left NULL --
        # every pre-existing row predates any other dashboard, so it *is* a
        # stocks row, and downstream queries filter on this column.
        for table in (
            "manual10_strategies",
            "manual10_activations",
            "manual10_days",
            "manual10_candidates",
            "manual10_positions",
        ):
            columns = _table_columns(conn, table)
            if "asset_class" not in columns:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN asset_class TEXT DEFAULT 'stocks'"
                )
                conn.execute(
                    f"UPDATE {table} SET asset_class = 'stocks' WHERE asset_class IS NULL"
                )

        # manual10_positions: contract-level fields for Options. All nullable
        # -- every existing stocks position (and every stocks query doing
        # `SELECT *`/`dict(row)`) is unaffected. leg_group_id ties a
        # multi-leg position's rows together (e.g. a covered call's stock
        # leg + short-call leg); leg_role distinguishes them ('stock' |
        # 'option' | 'single').
        position_columns = _table_columns(conn, "manual10_positions")
        for column, ddl in (
            ("underlying_symbol", "TEXT"),
            ("strike_price", "REAL"),
            ("expiration_date", "TEXT"),
            ("option_right", "TEXT"),
            ("contract_multiplier", "INTEGER"),
            ("leg_group_id", "TEXT"),
            ("leg_role", "TEXT"),
        ):
            if column not in position_columns:
                conn.execute(f"ALTER TABLE manual10_positions ADD COLUMN {column} {ddl}")

        conn.commit()
    finally:
        conn.close()


_init_schema()
_migrate_schema()


# ---------------------------------------------------------------------------
# Top 10's own settings
# ---------------------------------------------------------------------------

def get_settings() -> Dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM manual10_settings WHERE id = 1").fetchone()
        if not row:
            return dict(DEFAULT_SETTINGS)
        d = dict(row)
        d.pop("id", None)
        d.pop("updated_at", None)
        return d
    finally:
        conn.close()


def set_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    current = get_settings()
    current.update({k: v for k, v in values.items() if k in DEFAULT_SETTINGS})
    now = _now().isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO manual10_settings
                (id, screener_window_minutes, top_n, buy_in_per_stock, price_min, price_max,
                 promotion_window_minutes, close_out_minutes_before_close,
                 straggler_check_minutes_before_close, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                screener_window_minutes = excluded.screener_window_minutes,
                top_n = excluded.top_n,
                buy_in_per_stock = excluded.buy_in_per_stock,
                price_min = excluded.price_min,
                price_max = excluded.price_max,
                promotion_window_minutes = excluded.promotion_window_minutes,
                close_out_minutes_before_close = excluded.close_out_minutes_before_close,
                straggler_check_minutes_before_close = excluded.straggler_check_minutes_before_close,
                updated_at = excluded.updated_at
            """,
            (
                current["screener_window_minutes"], current["top_n"], current["buy_in_per_stock"],
                current["price_min"], current["price_max"], current["promotion_window_minutes"],
                current["close_out_minutes_before_close"], current["straggler_check_minutes_before_close"],
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return current


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

def list_strategies(asset_class: str = "stocks") -> List[Dict[str, Any]]:
    """Defaults to 'stocks' -- every existing caller (the Manual page's own
    API) passes nothing and must keep seeing exactly what it saw before
    Options started sharing this table, not a mixed roster. domain/options/
    repository.py is the only caller that passes 'options'."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM manual10_strategies WHERE asset_class = ? ORDER BY created_at ASC",
            (asset_class,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_all_strategy_keys() -> set:
    """Every strategy key across every asset class (unlike list_strategies(),
    which defaults to filtering by asset_class) -- `key` is this table's
    PRIMARY KEY table-wide, so a uniqueness check (`uploads.py::_unique_key`,
    and its Options counterpart) must see every existing key regardless of
    which dashboard created it, or two dashboards' uploads could collide on
    the same generated key."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT key FROM manual10_strategies").fetchall()
        return {r["key"] for r in rows}
    finally:
        conn.close()


def get_strategy_def(key: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM manual10_strategies WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_uploaded_strategy(
    *, key: str, name: str, description: str, code: str, interval_minutes: int,
    review_status: str, review_notes: str, asset_class: str = "stocks",
) -> Dict[str, Any]:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO manual10_strategies
                (key, name, kind, description, code, interval_minutes, review_status, review_notes,
                 created_at, asset_class)
            VALUES (?, ?, 'uploaded', ?, ?, ?, ?, ?, ?, ?)
            """,
            (key, name, description, code, interval_minutes, review_status, review_notes,
             _now().isoformat(), asset_class),
        )
        conn.commit()
    finally:
        conn.close()
    return get_strategy_def(key)


def delete_uploaded_strategy(key: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM manual10_strategies WHERE key = ? AND kind = 'uploaded'", (key,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Activation (select for today's panel, then explicitly activate to trade)
# ---------------------------------------------------------------------------

def get_activation(trading_date: str, strategy_key: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM manual10_activations WHERE trading_date = ? AND strategy_key = ?",
            (trading_date, strategy_key),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_activations(trading_date: str) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM manual10_activations WHERE trading_date = ?", (trading_date,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_selected(trading_date: str, strategy_key: str, selected: bool) -> Dict[str, Any]:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO manual10_activations (trading_date, strategy_key, selected, activated)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(trading_date, strategy_key) DO UPDATE SET selected = excluded.selected
            """,
            (trading_date, strategy_key, int(selected)),
        )
        conn.commit()
    finally:
        conn.close()
    return get_activation(trading_date, strategy_key)


def set_activated(trading_date: str, strategy_key: str, activated: bool) -> Dict[str, Any]:
    now = _now().isoformat() if activated else None
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO manual10_activations (trading_date, strategy_key, selected, activated, activated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(trading_date, strategy_key) DO UPDATE SET
                activated = excluded.activated,
                activated_at = excluded.activated_at,
                selected = 1
            """,
            (trading_date, strategy_key, int(activated), now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_activation(trading_date, strategy_key)


# ---------------------------------------------------------------------------
# Trading days (per strategy)
# ---------------------------------------------------------------------------

def get_day(trading_date: str, strategy_key: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM manual10_days WHERE trading_date = ? AND strategy_key = ?",
            (trading_date, strategy_key),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def ensure_day(trading_date: str, strategy_key: str) -> Dict[str, Any]:
    """Get today's row for this strategy, creating it (phase='pending') if
    this is the first touch of the day."""
    existing = get_day(trading_date, strategy_key)
    if existing:
        return existing
    now = _now().isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO manual10_days (trading_date, strategy_key, phase, created_at) VALUES (?, ?, 'pending', ?)",
            (trading_date, strategy_key, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_day(trading_date, strategy_key)


def update_day(trading_date: str, strategy_key: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE manual10_days SET {cols} WHERE trading_date = ? AND strategy_key = ?",
            (*fields.values(), trading_date, strategy_key),
        )
        conn.commit()
    finally:
        conn.close()


def list_days(strategy_key: Optional[str] = None, limit: int = 90) -> List[Dict[str, Any]]:
    """Most recent trading days first -- the calendar widget's data source.
    Unfiltered (`strategy_key=None`) rolls every active strategy's day rows
    together, since the wallet/calendar at the top of the page tracks overall
    performance across everything the user has activated, not one strategy
    at a time."""
    query = "SELECT * FROM manual10_days"
    params: List[Any] = []
    if strategy_key:
        query += " WHERE strategy_key = ?"
        params.append(strategy_key)
    query += " ORDER BY trading_date DESC LIMIT ?"
    params.append(limit)
    conn = _connect()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Candidates (screener output)
# ---------------------------------------------------------------------------

def save_candidates(trading_date: str, strategy_key: str, candidates: List[Dict[str, Any]]) -> None:
    conn = _connect()
    try:
        conn.executemany(
            """
            INSERT INTO manual10_candidates
                (trading_date, strategy_key, symbol, open_price, latest_price, change_pct, rank, picked)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trading_date, strategy_key, symbol) DO UPDATE SET
                open_price = excluded.open_price,
                latest_price = excluded.latest_price,
                change_pct = excluded.change_pct,
                rank = excluded.rank,
                picked = excluded.picked
            """,
            [
                (
                    trading_date, strategy_key, c["symbol"], c.get("open_price"), c.get("latest_price"),
                    c.get("change_pct"), c.get("rank"), int(c.get("picked", False)),
                )
                for c in candidates
            ],
        )
        conn.commit()
    finally:
        conn.close()


def list_candidates(trading_date: str, strategy_key: str) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM manual10_candidates WHERE trading_date = ? AND strategy_key = ?
            ORDER BY rank IS NULL, rank ASC, change_pct DESC
            """,
            (trading_date, strategy_key),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def open_position(
    *, trading_date: str, strategy_key: str, symbol: str, bucket: str, shares: float, entry_price: float,
    real_order_id: Optional[str] = None, promoted_from_paper: bool = False,
) -> int:
    now = _now().isoformat()
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO manual10_positions
                (trading_date, strategy_key, symbol, bucket, status, shares, entry_price, entry_time,
                 real_order_id, promoted_from_paper, updated_at)
            VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
            """,
            (trading_date, strategy_key, symbol, bucket, shares, entry_price, now, real_order_id, int(promoted_from_paper), now),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_position(position_id: int) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM manual10_positions WHERE id = ?", (position_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_positions(
    trading_date: str, strategy_key: Optional[str] = None, *, bucket: Optional[str] = None, status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM manual10_positions WHERE trading_date = ?"
    params: List[Any] = [trading_date]
    if strategy_key:
        query += " AND strategy_key = ?"
        params.append(strategy_key)
    if bucket:
        query += " AND bucket = ?"
        params.append(bucket)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY entry_time ASC"
    conn = _connect()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_position(position_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now().isoformat()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = _connect()
    try:
        conn.execute(f"UPDATE manual10_positions SET {cols} WHERE id = ?", (*fields.values(), position_id))
        conn.commit()
    finally:
        conn.close()


def close_position(position_id: int, *, exit_price: float, close_reason: str) -> None:
    update_position(
        position_id,
        status="closed",
        exit_price=exit_price,
        exit_time=_now().isoformat(),
        close_reason=close_reason,
    )


# ---------------------------------------------------------------------------
# Price snapshots (the "price N minutes ago" lookback)
# ---------------------------------------------------------------------------

def record_price_snapshot(trading_date: str, strategy_key: str, symbol: str, price: float, ts: Optional[str] = None) -> None:
    ts = ts or _now().isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO manual10_price_snapshots (strategy_key, symbol, trading_date, ts, price)
            VALUES (?, ?, ?, ?, ?)
            """,
            (strategy_key, symbol, trading_date, ts, price),
        )
        conn.commit()
    finally:
        conn.close()


def price_snapshot_near(trading_date: str, strategy_key: str, symbol: str, target_ts: str) -> Optional[float]:
    """The snapshot closest to (and at or before) `target_ts` -- used to answer
    "what was this stock's price ~10 minutes ago". Falls back to the earliest
    available snapshot if every recorded one is already after `target_ts`
    (early in the day, "10 minutes ago" may be before we started recording)."""
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT price FROM manual10_price_snapshots
            WHERE trading_date = ? AND strategy_key = ? AND symbol = ? AND ts <= ?
            ORDER BY ts DESC LIMIT 1
            """,
            (trading_date, strategy_key, symbol, target_ts),
        ).fetchone()
        if row:
            return float(row["price"])
        row = conn.execute(
            """
            SELECT price FROM manual10_price_snapshots
            WHERE trading_date = ? AND strategy_key = ? AND symbol = ?
            ORDER BY ts ASC LIMIT 1
            """,
            (trading_date, strategy_key, symbol),
        ).fetchone()
        return float(row["price"]) if row else None
    finally:
        conn.close()
