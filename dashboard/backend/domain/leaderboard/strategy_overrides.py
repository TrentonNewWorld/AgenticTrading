"""Per-strategy parameter overrides for the Strategy Catalog's Edit page.

A strategy class optionally declares ``PARAM_SCHEMA`` (see
``strategies/base.py``) naming the constants it is safe to tune from the UI
-- e.g. how many names Momentum Effect holds, or how many days Blue-Chip
Steady waits between rebalances. Overrides saved here are merged into that
strategy's config by ``catalog._config_for``/``_catalog_roster`` before the
strategy is constructed, so both the catalog's own backtest curve and its
Run in Paper/Run in Live buttons reflect an edit immediately -- there is no
separate "apply" step.

Only keys present in the strategy's own PARAM_SCHEMA are ever accepted
(enforced by ``set_overrides``); a strategy with an empty PARAM_SCHEMA (most
of the passive/optimizer baselines -- mean_variance_djia, equal_weight_djia,
spy_index, djia_index) has nothing to tune here.

Persisted in the same SQLite file as everything else (DATABASE_PATH), in one
table this module owns outright: `strategy_param_overrides`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict

from dashboard.backend.database import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_param_overrides (
                strategy_key TEXT PRIMARY KEY,
                overrides_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


_init_schema()


def get_overrides(strategy_key: str) -> Dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT overrides_json FROM strategy_param_overrides WHERE strategy_key = ?",
            (strategy_key,),
        ).fetchone()
        return json.loads(row["overrides_json"]) if row else {}
    finally:
        conn.close()


def set_overrides(strategy_key: str, schema: Dict[str, Dict[str, Any]], values: Dict[str, Any]) -> Dict[str, Any]:
    """Validate `values` against `schema` (the strategy's own PARAM_SCHEMA)
    and persist them. Unknown keys are rejected rather than silently dropped
    -- a typo'd param name should fail loudly, not vanish into a config
    dict the strategy never reads. Returns the stored overrides."""
    if not schema:
        raise ValueError("this strategy has no tunable parameters")
    cleaned: Dict[str, Any] = {}
    for key, raw in values.items():
        if key not in schema:
            raise ValueError(f"'{key}' is not a tunable parameter for this strategy")
        spec = schema[key]
        try:
            value = float(raw) if spec.get("type") == "float" else int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' must be a number")
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and value < lo:
            raise ValueError(f"'{key}' must be >= {lo}")
        if hi is not None and value > hi:
            raise ValueError(f"'{key}' must be <= {hi}")
        cleaned[key] = value

    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO strategy_param_overrides (strategy_key, overrides_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                overrides_json = excluded.overrides_json,
                updated_at = excluded.updated_at
            """,
            (strategy_key, json.dumps(cleaned), now),
        )
        conn.commit()
    finally:
        conn.close()
    return cleaned


def schema_for(strategy_key: str) -> Dict[str, Dict[str, Any]]:
    """This strategy class's PARAM_SCHEMA, looked up by its registry key --
    empty for any strategy that doesn't declare one."""
    from dashboard.backend.domain.leaderboard.strategies import available_strategies

    cls = available_strategies().get(strategy_key)
    return dict(cls.PARAM_SCHEMA) if cls is not None else {}


def effective_params(strategy_key: str) -> Dict[str, Any]:
    """This strategy's tunable params at their current effective values: a
    saved override where one exists, the schema's own default otherwise."""
    schema = schema_for(strategy_key)
    overrides = get_overrides(strategy_key)
    return {name: overrides.get(name, spec["default"]) for name, spec in schema.items()}


def apply_overrides(strategy_key: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Merge this strategy's saved parameter overrides into `config` before
    it's used to construct the strategy -- the one place both the catalog's
    backtest preview (``domain/leaderboard/catalog.py``) and actual paper/live
    runs (``execution/alpaca_paper_service.py``, ``alpaca_live_service.py``)
    go through, so an edit on the Strategy Catalog page takes effect
    identically everywhere the strategy runs, with no separate "apply" step."""
    overrides = get_overrides(strategy_key)
    if not overrides:
        return config
    merged = dict(config)
    merged.update(overrides)
    return merged
