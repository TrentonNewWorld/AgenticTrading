"""Thin wrapper around manual10's shared tables, scoped to
``asset_class='crypto'`` -- mirrors domain/futures/repository.py and
domain/forex/repository.py exactly.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from dashboard.backend.domain.manual10 import repository as manual10_repo

ASSET_CLASS = "crypto"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "strategy"


def _unique_key(base_slug: str) -> str:
    existing = manual10_repo.list_all_strategy_keys()
    candidate = f"cx_{base_slug}"
    n = 2
    while candidate in existing:
        candidate = f"cx_{base_slug}_{n}"
        n += 1
    return candidate


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


def get_activation(trading_date: str, strategy_key: str) -> Optional[Dict[str, Any]]:
    return manual10_repo.get_activation(trading_date, strategy_key)


def list_activations(trading_date: str) -> List[Dict[str, Any]]:
    crypto_keys = {s["key"] for s in list_strategies()}
    return [a for a in manual10_repo.list_activations(trading_date) if a["strategy_key"] in crypto_keys]


def set_selected(trading_date: str, strategy_key: str, selected: bool) -> Dict[str, Any]:
    return manual10_repo.set_selected(trading_date, strategy_key, selected)


def set_activated(trading_date: str, strategy_key: str, activated: bool) -> Dict[str, Any]:
    return manual10_repo.set_activated(trading_date, strategy_key, activated)


def get_day(trading_date: str, strategy_key: str) -> Optional[Dict[str, Any]]:
    return manual10_repo.get_day(trading_date, strategy_key)


def ensure_day(trading_date: str, strategy_key: str) -> Dict[str, Any]:
    return manual10_repo.ensure_day(trading_date, strategy_key)


def update_day(trading_date: str, strategy_key: str, **fields: Any) -> None:
    manual10_repo.update_day(trading_date, strategy_key, **fields)


def list_days(strategy_key: Optional[str] = None, limit: int = 90) -> List[Dict[str, Any]]:
    if strategy_key is not None:
        return manual10_repo.list_days(strategy_key=strategy_key, limit=limit)
    crypto_keys = {s["key"] for s in list_strategies()}
    rows = manual10_repo.list_days(limit=limit * 10)
    return [d for d in rows if d["strategy_key"] in crypto_keys][:limit]


def open_position(
    *, trading_date: str, strategy_key: str, symbol: str, bucket: str, shares: float, entry_price: float,
    real_order_id: Optional[str] = None, promoted_from_paper: bool = False,
) -> int:
    position_id = manual10_repo.open_position(
        trading_date=trading_date, strategy_key=strategy_key, symbol=symbol, bucket=bucket,
        shares=shares, entry_price=entry_price, real_order_id=real_order_id,
        promoted_from_paper=promoted_from_paper,
    )
    manual10_repo.update_position(position_id, asset_class=ASSET_CLASS)
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


def record_price_snapshot(trading_date: str, strategy_key: str, symbol: str, price: float, ts: Optional[str] = None) -> None:
    manual10_repo.record_price_snapshot(trading_date, strategy_key, symbol, price, ts=ts)


def price_snapshot_near(trading_date: str, strategy_key: str, symbol: str, target_ts: str) -> Optional[float]:
    return manual10_repo.price_snapshot_near(trading_date, strategy_key, symbol, target_ts)
