"""Sub-phase 0 of the Options-dashboard plan: every table that Stocks,
Options, and (later) Futures/Forex/Crypto will share needs an ``asset_class``
discriminator, added as a lazy migration (not just a fresh-install CREATE
TABLE column) since this repo's own local dev DB -- and every deployed one --
predates it. These tests pin both halves: a fresh database gets the column,
and a pre-existing on-disk database gets it added and backfilled to
``'stocks'`` without losing existing rows.
"""

from __future__ import annotations

import sqlite3

import pytest

from dashboard.backend.database import BacktestDatabase


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# ---------------------------------------------------------------------------
# agent_runs (database.py)
# ---------------------------------------------------------------------------

def test_agent_runs_fresh_database_has_asset_class(tmp_path):
    db = BacktestDatabase(tmp_path / "fresh.db")
    conn = db._get_connection()
    try:
        assert "asset_class" in _columns(conn, "agent_runs")
    finally:
        conn.close()


def test_agent_runs_migrates_pre_existing_database(tmp_path):
    """A DB created before asset_class existed gets the column added and
    backfilled, not left NULL (downstream ranking/lookup code filters on
    this column, so a NULL would silently drop old runs off every board)."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE agent_runs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            mode TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            initial_equity REAL NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO agent_runs (run_id, session_id, agent_name, mode, start_date, end_date, initial_equity) "
        "VALUES ('legacy_run', 'sess', 'agent', 'external', '2025-01-01', '2025-12-31', 1000.0)"
    )
    conn.commit()
    conn.close()

    db = BacktestDatabase(db_path)
    conn = db._get_connection()
    try:
        assert "asset_class" in _columns(conn, "agent_runs")
        row = conn.execute(
            "SELECT asset_class FROM agent_runs WHERE run_id = 'legacy_run'"
        ).fetchone()
        assert row[0] == "stocks"
    finally:
        conn.close()


def test_insert_run_defaults_asset_class_to_stocks(tmp_path):
    db = BacktestDatabase(tmp_path / "insert.db")
    db.insert_run(
        run_id="r1", session_id="s1", agent_name="a1", mode="external",
        start_date="2025-01-01", end_date="2025-12-31", initial_equity=1000.0,
    )
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT asset_class FROM agent_runs WHERE run_id = 'r1'").fetchone()
        assert row[0] == "stocks"
    finally:
        conn.close()

    db.insert_run(
        run_id="r2", session_id="s2", agent_name="a2", mode="external",
        start_date="2025-01-01", end_date="2025-12-31", initial_equity=1000.0,
        asset_class="options",
    )
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT asset_class FROM agent_runs WHERE run_id = 'r2'").fetchone()
        assert row[0] == "options"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# manual10 tables (domain/manual10/repository.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def manual10_repo(monkeypatch, tmp_path):
    import dashboard.backend.domain.manual10.repository as repo_module
    monkeypatch.setattr(repo_module, "DB_PATH", str(tmp_path / "manual10.db"))
    repo_module._init_schema()
    repo_module._migrate_schema()
    return repo_module


_MANUAL10_TABLES = (
    "manual10_strategies",
    "manual10_activations",
    "manual10_days",
    "manual10_candidates",
    "manual10_positions",
)

_MANUAL10_POSITION_CONTRACT_COLUMNS = (
    "underlying_symbol", "strike_price", "expiration_date", "option_right",
    "contract_multiplier", "leg_group_id", "leg_role",
)


def test_manual10_fresh_database_has_asset_class_everywhere(manual10_repo):
    conn = manual10_repo._connect()
    try:
        for table in _MANUAL10_TABLES:
            assert "asset_class" in _columns(conn, table), table
        for column in _MANUAL10_POSITION_CONTRACT_COLUMNS:
            assert column in _columns(conn, "manual10_positions"), column
    finally:
        conn.close()


def test_manual10_migrates_pre_existing_database(monkeypatch, tmp_path):
    """A manual10 DB created before this migration (this exact schema is
    what shipped before the Options-dashboard plan) gets asset_class added
    to every table and existing rows backfilled to 'stocks', without losing
    the seeded builtin Top 10 strategy row."""
    db_path = tmp_path / "legacy_manual10.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE manual10_strategies (
            key TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
            description TEXT, code TEXT, interval_minutes INTEGER,
            review_status TEXT NOT NULL DEFAULT 'approved', review_notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO manual10_strategies (key, name, kind, review_status, created_at) "
        "VALUES ('top_10', 'Top 10', 'builtin', 'approved', '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        """
        CREATE TABLE manual10_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trading_date TEXT NOT NULL,
            strategy_key TEXT NOT NULL, symbol TEXT NOT NULL, bucket TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', shares REAL NOT NULL,
            entry_price REAL NOT NULL, entry_time TEXT NOT NULL, exit_price REAL,
            exit_time TEXT, close_reason TEXT, real_order_id TEXT,
            promoted_from_paper INTEGER NOT NULL DEFAULT 0,
            promotion_checked INTEGER NOT NULL DEFAULT 0, promotion_note TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO manual10_positions "
        "(trading_date, strategy_key, symbol, bucket, shares, entry_price, entry_time, updated_at) "
        "VALUES ('2026-08-21', 'top_10', 'AAPL', 'paper', 10, 150.0, '2026-08-21T14:30:00+00:00', '2026-08-21T14:30:00+00:00')"
    )
    conn.commit()
    conn.close()

    import dashboard.backend.domain.manual10.repository as repo_module
    monkeypatch.setattr(repo_module, "DB_PATH", str(db_path))
    repo_module._init_schema()
    repo_module._migrate_schema()

    conn = repo_module._connect()
    try:
        assert "asset_class" in _columns(conn, "manual10_strategies")
        assert "asset_class" in _columns(conn, "manual10_positions")
        for column in _MANUAL10_POSITION_CONTRACT_COLUMNS:
            assert column in _columns(conn, "manual10_positions")

        strategy_row = conn.execute(
            "SELECT asset_class FROM manual10_strategies WHERE key = 'top_10'"
        ).fetchone()
        assert strategy_row["asset_class"] == "stocks"

        position_row = conn.execute(
            "SELECT asset_class, symbol, underlying_symbol, leg_group_id FROM manual10_positions"
        ).fetchone()
        assert position_row["asset_class"] == "stocks"
        assert position_row["symbol"] == "AAPL"  # existing row untouched
        assert position_row["underlying_symbol"] is None  # new columns nullable
        assert position_row["leg_group_id"] is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# strategy_test_queue (domain/strategy_testing/repository.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def strategy_testing_repo(monkeypatch, tmp_path):
    import dashboard.backend.domain.strategy_testing.repository as repo_module
    monkeypatch.setattr(repo_module, "DB_PATH", str(tmp_path / "strategy_testing.db"))
    repo_module.init_schema()
    return repo_module


def test_strategy_test_queue_fresh_database_has_asset_class(strategy_testing_repo):
    conn = strategy_testing_repo._get_connection()
    try:
        assert "asset_class" in _columns(conn, "strategy_test_queue")
    finally:
        conn.close()


def test_strategy_test_queue_migrates_pre_existing_database(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy_queue.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE strategy_test_queue (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
            code TEXT NOT NULL, source_filename TEXT,
            status TEXT NOT NULL DEFAULT 'queued', submitted_at TEXT NOT NULL,
            started_at TEXT, finished_at TEXT, scan_verdict TEXT, scan_notes TEXT,
            scan_is_strategy INTEGER, error TEXT, result_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO strategy_test_queue (id, name, code, status, submitted_at) "
        "VALUES ('row1', 'My Strategy', 'def decide(x): return {}', 'ready', '2026-08-21T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    import dashboard.backend.domain.strategy_testing.repository as repo_module
    monkeypatch.setattr(repo_module, "DB_PATH", str(db_path))
    repo_module.init_schema()

    conn = repo_module._get_connection()
    try:
        assert "asset_class" in _columns(conn, "strategy_test_queue")
        row = conn.execute(
            "SELECT asset_class, name FROM strategy_test_queue WHERE id = 'row1'"
        ).fetchone()
        assert row["asset_class"] == "stocks"
        assert row["name"] == "My Strategy"  # existing row untouched
    finally:
        conn.close()
