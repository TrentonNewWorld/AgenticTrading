"""Strategy Catalog: every registered strategy that cleared 3%+ in the
"Strategy Lab" full-year backtest, with a description, an equity-curve
chart, and buttons to run it for real via paper or live trading.

The paper/live run routes are thin wrappers around the existing
``alpaca_paper_service.run_paper_for_strategy`` /
``alpaca_live_service.run_live_for_strategy`` functions -- both already
default to a dry-run review cycle and require their own env-var kill switch
(``ALPACA_PAPER_EXECUTE`` / ``ALPACA_LIVE_EXECUTE``) to place a real order.
Nothing here changes that gating; this router only exposes it over HTTP so
the catalog page can offer a "Run" button instead of requiring the CLI.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from dashboard.backend.api.auth import get_current_user_optional
from dashboard.backend.domain.leaderboard import catalog_activation
from dashboard.backend.domain.leaderboard.catalog import (
    build_export,
    generate_report,
    get_strategy_catalog,
    remove_strategy,
    resolve_run_config,
)
from dashboard.backend.domain.leaderboard.real_trading import (
    get_allocation,
    get_per_stock_amount,
    set_allocation,
    set_per_stock_amount,
)
from dashboard.backend.domain.leaderboard.strategy_overrides import (
    effective_params,
    schema_for,
    set_overrides,
)
from dashboard.backend.execution import alpaca_live_service, alpaca_paper_service

router = APIRouter(prefix="/v1/strategy-catalog", tags=["strategy-catalog"])


@router.get("")
def list_catalog(refresh: bool = False):
    return get_strategy_catalog(force_refresh=refresh)


@router.delete("/{key}")
def delete_catalog_strategy(key: str):
    try:
        removed = remove_strategy(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not removed:
        raise HTTPException(status_code=404, detail=f"no strategy '{key}' in the catalog")
    return {"removed": key}


class AllocationBody(BaseModel):
    allocated_capital: Optional[float] = None
    #: None means "no fixed amount" -- each held symbol gets a proportional
    #: share of allocated_capital instead. Omit this field (not 0) to leave
    #: whatever's already set unchanged; pass it explicitly as null to clear
    #: an existing fixed amount.
    per_stock_amount: Optional[float] = None


def _allocation_payload(key: str) -> Dict[str, Any]:
    return {
        "key": key,
        "allocated_capital": get_allocation(key),
        "per_stock_amount": get_per_stock_amount(key),
    }


@router.get("/{key}/allocation")
def get_strategy_allocation(key: str):
    return _allocation_payload(key)


@router.put("/{key}/allocation")
def put_strategy_allocation(key: str, body: AllocationBody):
    """Sets the real dollar amount this strategy is allowed to trade with.

    ``allocated_capital`` is a real cap, not just a display number: both
    Run in Paper and Run in Live size every new purchase against
    ``min(allocated_capital, what the account can actually afford)`` instead
    of the whole shared account's cash + holdings (see
    execution/_alpaca_strategy_shared.py::capped_portfolio_value) -- this is
    also the same number the Real Trading Leaderboard tracks against.

    ``per_stock_amount``, if set, fixes exactly how much goes into each
    symbol the strategy decides to hold (like a per-position lot size)
    instead of splitting the allocation proportionally by the strategy's own
    target weights -- see effective_target_weights."""
    fields_set = body.model_fields_set
    if not fields_set:
        raise HTTPException(status_code=400, detail="no fields provided")
    try:
        if "allocated_capital" in fields_set:
            if body.allocated_capital is None:
                raise HTTPException(status_code=400, detail="allocated_capital cannot be null")
            set_allocation(key, body.allocated_capital)
        if "per_stock_amount" in fields_set:
            set_per_stock_amount(key, body.per_stock_amount)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _allocation_payload(key)


@router.get("/{key}/params")
def get_strategy_params(key: str):
    """The Edit page's data source: this strategy's tunable-parameter schema
    (label/type/min/max/default per param) plus each one's current effective
    value -- a saved override where one exists, the schema default otherwise.
    An empty ``schema`` means this strategy has nothing to tune here."""
    return {"key": key, "schema": schema_for(key), "values": effective_params(key)}


class ParamsBody(BaseModel):
    values: Dict[str, Any]


@router.put("/{key}/params")
def put_strategy_params(key: str, body: ParamsBody):
    """Save real tunable-parameter overrides for this strategy. Takes effect
    immediately everywhere the strategy runs -- the catalog's own backtest
    curve (next recompute) and both Run in Paper/Run in Live -- since both
    paths merge these overrides in via ``strategy_overrides.apply_overrides``
    before constructing the strategy. No separate "apply" step."""
    schema = schema_for(key)
    try:
        set_overrides(key, schema, body.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"key": key, "schema": schema, "values": effective_params(key)}


class RunRequestBody(BaseModel):
    #: Always defaults to a dry run. A client CAN pass `dry_run: false`, but
    #: that alone is not enough to place a real order -- both services also
    #: require their own ALPACA_*_EXECUTE env var to be armed server-side.
    dry_run: bool = True
    symbols: Optional[list[str]] = None


@router.get("/{key}/report")
def get_strategy_report(key: str):
    """Month-by-month + overall P&L, always at a $1,000 starting wallet over
    the most recent completed year -- the "Reports" button's data."""
    try:
        return generate_report(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/{key}/export")
def export_strategy(key: str):
    try:
        package = build_export(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    filename = f"{(package.get('name') or key).strip().lower().replace(' ', '_')}.strategy.json"
    return JSONResponse(content=package, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/{key}/paper")
async def run_paper(
    key: str, body: RunRequestBody,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    try:
        return await alpaca_paper_service.run_paper_for_strategy(
            strategy_key=key, symbols=body.symbols, dry_run=body.dry_run,
            user_id=current_user["id"] if current_user else None,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{key}/live")
async def run_live(
    key: str, body: RunRequestBody,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    try:
        return await alpaca_live_service.run_live_for_strategy(
            strategy_key=key, symbols=body.symbols, dry_run=body.dry_run,
            user_id=current_user["id"] if current_user else None,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _require_catalog_key(key: str) -> None:
    try:
        resolve_run_config(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{key}/activation")
def get_activation(key: str):
    """Current paper/live "keep running until turned off" state for one
    strategy -- what the catalog card's buttons render from. Separate from
    the cached ``GET /strategy-catalog`` list on purpose: activation state
    changes independently of (and far more often than) the backtest-curve
    cache that route serves."""
    _require_catalog_key(key)
    return {
        "paper": catalog_activation.get(key, "paper") or {"strategy_key": key, "mode": "paper", "activated": False},
        "live": catalog_activation.get(key, "live") or {"strategy_key": key, "mode": "live", "activated": False},
    }


@router.post("/{key}/paper/activate")
def activate_paper(key: str, current_user: Optional[dict] = Depends(get_current_user_optional)):
    _require_catalog_key(key)
    return catalog_activation.activate(key, "paper", current_user["id"] if current_user else None)


@router.post("/{key}/paper/deactivate")
def deactivate_paper(key: str):
    _require_catalog_key(key)
    return catalog_activation.deactivate(key, "paper")


@router.post("/{key}/live/activate")
def activate_live(key: str, current_user: Optional[dict] = Depends(get_current_user_optional)):
    _require_catalog_key(key)
    return catalog_activation.activate(key, "live", current_user["id"] if current_user else None)


@router.post("/{key}/live/deactivate")
def deactivate_live(key: str):
    _require_catalog_key(key)
    return catalog_activation.deactivate(key, "live")
