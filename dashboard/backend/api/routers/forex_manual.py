"""Forex Manual page: the multi-strategy manual-trading page's HTTP surface
for the Forex dashboard. Mirrors api/routers/futures_manual.py's shape
exactly.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dashboard.backend.api.auth import get_current_user_optional
from dashboard.backend.domain.forex import engine, market_clock, repository as repo, uploads, views

router = APIRouter(prefix="/v1/forex/manual", tags=["forex-manual"])


def _today_str() -> str:
    session = market_clock.get_today_session()
    return str(session.trading_date)


@router.get("/status")
def get_status():
    trading_date = _today_str()
    return views.wallet_summary(trading_date)


@router.get("/strategies")
def list_strategies():
    trading_date = _today_str()
    out = []
    for strategy in repo.list_strategies():
        status = views.strategy_status(trading_date, strategy["key"])
        out.append({**strategy, **status})
    return {"strategies": out}


class UploadBody(BaseModel):
    name: str = Field(..., max_length=100)
    description: str = Field("", max_length=2000)
    code: str = Field(..., max_length=50_000)
    interval_minutes: int = 15


@router.post("/strategies/upload")
def upload_strategy(body: UploadBody, current_user: Optional[dict] = Depends(get_current_user_optional)):
    try:
        return uploads.submit_upload(
            name=body.name, description=body.description, code=body.code, interval_minutes=body.interval_minutes,
            user_id=current_user["id"] if current_user else None,
        )
    except uploads.UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/strategies/{key}/approve")
def approve_strategy(key: str):
    try:
        return uploads.approve_upload(key)
    except uploads.UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/strategies/{key}/reject")
def reject_strategy(key: str):
    try:
        return uploads.reject_upload(key)
    except uploads.UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/strategies/{key}")
def delete_strategy(key: str):
    if not repo.delete_uploaded_strategy(key):
        raise HTTPException(status_code=404, detail=f"no uploaded forex strategy '{key}'")
    return {"deleted": key}


def _require_strategy(key: str) -> dict:
    strategy = repo.get_strategy_def(key)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"no strategy '{key}'")
    return strategy


@router.post("/strategies/{key}/select")
def select_strategy(key: str):
    _require_strategy(key)
    trading_date = _today_str()
    return repo.set_selected(trading_date, key, True)


@router.post("/strategies/{key}/deselect")
def deselect_strategy(key: str):
    trading_date = _today_str()
    return repo.set_selected(trading_date, key, False)


@router.post("/strategies/{key}/activate")
def activate_strategy(key: str):
    strategy = _require_strategy(key)
    if strategy["review_status"] != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"this strategy is {strategy['review_status']}, not approved -- it can't be activated yet",
        )
    trading_date = _today_str()
    return repo.set_activated(trading_date, key, True)


@router.post("/strategies/{key}/deactivate")
def deactivate_strategy(key: str):
    trading_date = _today_str()
    return repo.set_activated(trading_date, key, False)


@router.get("/positions")
def get_positions(strategy_key: str, bucket: Optional[str] = None, status: Optional[str] = None):
    if bucket not in (None, "paper", "real"):
        raise HTTPException(status_code=400, detail="bucket must be 'paper' or 'real'")
    trading_date = _today_str()
    return {
        "trading_date": trading_date, "strategy_key": strategy_key,
        "positions": views.enrich_positions(trading_date, strategy_key, bucket=bucket, status=status),
    }


@router.get("/calendar")
def get_calendar(limit: int = 90):
    return {"days": views.calendar(limit=limit)}


@router.post("/positions/{position_id}/sell")
def sell_position(position_id: int):
    try:
        return engine.manual_sell(position_id)
    except engine.ManualActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
