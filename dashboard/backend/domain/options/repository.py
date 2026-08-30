"""Thin wrapper around manual10's shared tables (Sub-phase 0 schema), scoped
to ``asset_class='options'`` -- one schema, one set of SQL
(``domain/manual10/repository.py`` owns the actual table definitions and
CRUD), two thin call surfaces, rather than a duplicated schema for a second
dashboard.

Strategy keys are namespaced with an ``opt_`` prefix (see ``_unique_key``
below) -- that, not the ``asset_class`` column, is what actually prevents
Options and Stocks rows from colliding in the tables keyed only on
``(trading_date, strategy_key)``. ``asset_class`` is for filtering/display
(so the Options dashboard's own strategy list never shows a Stocks upload),
not for uniqueness -- widening those tables' primary keys to include it would
have broken the ``{key}`` URL path segment used throughout
``api/routers/manual10.py``, per Sub-phase 0's migration notes.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from dashboard.backend.domain.manual10 import repository as manual10_repo

ASSET_CLASS = "options"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "strategy"


def _unique_key(base_slug: str) -> str:
    existing = manual10_repo.list_all_strategy_keys()
    candidate = f"opt_{base_slug}"
    n = 2
    while candidate in existing:
        candidate = f"opt_{base_slug}_{n}"
        n += 1
    return candidate


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

def list_strategies() -> List[Dict[str, Any]]:
    return manual10_repo.list_strategies(asset_class=ASSET_CLASS)


def get_strategy_def(key: str) -> Optional[Dict[str, Any]]:
    strategy = manual10_repo.get_strategy_def(key)
    if strategy is None or strategy.get("asset_class") != ASSET_CLASS:
        return None
    return strategy


def create_uploaded_strategy(
    *, name: str, description: str, code: str, interval_minutes: int,
    review_status: str, review_notes: str,
) -> Dict[str, Any]:
    key = _unique_key(_slugify(name))
    return manual10_repo.create_uploaded_strategy(
        key=key, name=name, description=description, code=code,
        interval_minutes=interval_minutes, review_status=review_status,
        review_notes=review_notes, asset_class=ASSET_CLASS,
    )


def delete_uploaded_strategy(key: str) -> bool:
    strategy = get_strategy_def(key)
    if strategy is None:
        return False
    return manual10_repo.delete_uploaded_strategy(key)


# ---------------------------------------------------------------------------
# Activation (select for today's panel, then explicitly activate to trade)
# ---------------------------------------------------------------------------

def get_activation(trading_date: str, strategy_key: str) -> Optional[Dict[str, Any]]:
    return manual10_repo.get_activation(trading_date, strategy_key)


def list_activations(trading_date: str) -> List[Dict[str, Any]]:
    option_keys = {s["key"] for s in list_strategies()}
    return [a for a in manual10_repo.list_activations(trading_date) if a["strategy_key"] in option_keys]


def set_selected(trading_date: str, strategy_key: str, selected: bool) -> Dict[str, Any]:
    return manual10_repo.set_selected(trading_date, strategy_key, selected)


def set_activated(trading_date: str, strategy_key: str, activated: bool) -> Dict[str, Any]:
    return manual10_repo.set_activated(trading_date, strategy_key, activated)


# ---------------------------------------------------------------------------
# Trading days (per strategy)
# ---------------------------------------------------------------------------

def get_day(trading_date: str, strategy_key: str) -> Optional[Dict[str, Any]]:
    return manual10_repo.get_day(trading_date, strategy_key)


def ensure_day(trading_date: str, strategy_key: str) -> Dict[str, Any]:
    return manual10_repo.ensure_day(trading_date, strategy_key)


def update_day(trading_date: str, strategy_key: str, **fields: Any) -> None:
    manual10_repo.update_day(trading_date, strategy_key, **fields)


def list_days(strategy_key: Optional[str] = None, limit: int = 90) -> List[Dict[str, Any]]:
    if strategy_key is not None:
        return manual10_repo.list_days(strategy_key=strategy_key, limit=limit)
    option_keys = {s["key"] for s in list_strategies()}
    # Over-fetch then filter+truncate -- list_days has no asset_class filter
    # of its own (see module docstring on why the shared tables aren't
    # widened), so this pulls a larger unfiltered window and narrows client-
    # side. Acceptable here: this path only serves the wallet/calendar
    # aggregate view, not a hot per-tick loop.
    rows = manual10_repo.list_days(limit=limit * 10)
    return [d for d in rows if d["strategy_key"] in option_keys][:limit]


# ---------------------------------------------------------------------------
# Candidates (unused by Options today -- no screener -- kept for parity with
# manual10_repo's surface in case a future Options screener needs it)
# ---------------------------------------------------------------------------

def save_candidates(trading_date: str, strategy_key: str, candidates: List[Dict[str, Any]]) -> None:
    manual10_repo.save_candidates(trading_date, strategy_key, candidates)


def list_candidates(trading_date: str, strategy_key: str) -> List[Dict[str, Any]]:
    return manual10_repo.list_candidates(trading_date, strategy_key)


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def open_position(
    *, trading_date: str, strategy_key: str, symbol: str, bucket: str, shares: float, entry_price: float,
    real_order_id: Optional[str] = None, promoted_from_paper: bool = False,
    underlying_symbol: Optional[str] = None, strike_price: Optional[float] = None,
    expiration_date: Optional[str] = None, option_right: Optional[str] = None,
    contract_multiplier: Optional[int] = None, leg_group_id: Optional[str] = None,
    leg_role: Optional[str] = None,
) -> int:
    position_id = manual10_repo.open_position(
        trading_date=trading_date, strategy_key=strategy_key, symbol=symbol, bucket=bucket,
        shares=shares, entry_price=entry_price, real_order_id=real_order_id,
        promoted_from_paper=promoted_from_paper,
    )
    # manual10_repo.open_position doesn't know about the contract-level
    # columns (they're Options-only) -- set them in a follow-up UPDATE rather
    # than widening that function's signature for a shape only this caller uses.
    extra = {
        "asset_class": ASSET_CLASS,
        "underlying_symbol": underlying_symbol,
        "strike_price": strike_price,
        "expiration_date": expiration_date,
        "option_right": option_right,
        "contract_multiplier": contract_multiplier,
        "leg_group_id": leg_group_id,
        "leg_role": leg_role,
    }
    manual10_repo.update_position(position_id, **{k: v for k, v in extra.items() if v is not None or k == "asset_class"})
    return position_id


def get_position(position_id: int) -> Optional[Dict[str, Any]]:
    return manual10_repo.get_position(position_id)


def list_positions(
    trading_date: str, strategy_key: Optional[str] = None, *, bucket: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return manual10_repo.list_positions(trading_date, strategy_key, bucket=bucket, status=status)


def update_position(position_id: int, **fields: Any) -> None:
    manual10_repo.update_position(position_id, **fields)


def close_position(position_id: int, *, exit_price: float, close_reason: str) -> None:
    manual10_repo.close_position(position_id, exit_price=exit_price, close_reason=close_reason)


# ---------------------------------------------------------------------------
# Price snapshots
# ---------------------------------------------------------------------------

def record_price_snapshot(trading_date: str, strategy_key: str, symbol: str, price: float, ts: Optional[str] = None) -> None:
    manual10_repo.record_price_snapshot(trading_date, strategy_key, symbol, price, ts=ts)


def price_snapshot_near(trading_date: str, strategy_key: str, symbol: str, target_ts: str) -> Optional[float]:
    return manual10_repo.price_snapshot_near(trading_date, strategy_key, symbol, target_ts)
