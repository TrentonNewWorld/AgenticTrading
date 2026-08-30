"""Manual: the multi-strategy manual-trading page's HTTP surface.

Read routes (status/settings/screener/positions/calendar/strategies) are
always safe -- they only ever read the ledger and take a live quote. The
mutating ones (settings, upload, approve/reject, select/activate, the three
manual position actions) go through domain/manual10/{engine,uploads}.py,
which is where every real-money and code-safety gate actually lives; this
router does no gating decisions of its own.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dashboard.backend.api.auth import get_current_user_optional
from dashboard.backend.domain.manual10 import engine, market_clock, repository as repo, scheduler, uploads, views
from dashboard.backend.domain.manual10.repository import TOP_10_STRATEGY_KEY

router = APIRouter(prefix="/v1/manual10", tags=["manual10"])


def _today_str() -> str:
    """Just a repository lookup key -- deliberately doesn't call
    market_clock.get_today_session() (which needs live Alpaca calendar
    access) since none of this router's date-keyed reads/writes need
    open/close/in-session state, only the calendar date itself. See
    market_clock.today_trading_date()'s docstring."""
    return str(market_clock.today_trading_date())


@router.get("/status")
def get_status():
    trading_date = _today_str()
    summary = views.wallet_summary(trading_date)
    summary["scheduler_running"] = scheduler.is_running()
    return summary


class SettingsBody(BaseModel):
    screener_window_minutes: Optional[int] = None
    top_n: Optional[int] = None
    buy_in_per_stock: Optional[float] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    promotion_window_minutes: Optional[int] = None
    close_out_minutes_before_close: Optional[int] = None
    straggler_check_minutes_before_close: Optional[int] = None


@router.get("/settings")
def get_settings():
    return repo.get_settings()


@router.put("/settings")
def put_settings(body: SettingsBody):
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    if not values:
        raise HTTPException(status_code=400, detail="no settings provided")
    for int_field in ("screener_window_minutes", "top_n", "promotion_window_minutes",
                      "close_out_minutes_before_close", "straggler_check_minutes_before_close"):
        if int_field in values and values[int_field] <= 0:
            raise HTTPException(status_code=400, detail=f"{int_field} must be positive")
    if "buy_in_per_stock" in values and values["buy_in_per_stock"] <= 0:
        raise HTTPException(status_code=400, detail="buy_in_per_stock must be positive")
    if "price_min" in values and "price_max" in values and values["price_min"] >= values["price_max"]:
        raise HTTPException(status_code=400, detail="price_min must be less than price_max")
    return repo.set_settings(values)


# ---------------------------------------------------------------------------
# Strategy registry: browse, upload, review
# ---------------------------------------------------------------------------

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
    """Submit uploaded strategy code. Always lands as review_status=pending
    -- see domain/manual10/uploads.py for why an LLM risk review never
    auto-approves; a human must call /approve before this strategy can even
    be selected."""
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
        raise HTTPException(status_code=404, detail=f"no uploaded strategy '{key}' (built-in strategies can't be deleted)")
    return {"deleted": key}


# ---------------------------------------------------------------------------
# Select (open a strategy's panel for today) / activate (let it actually trade)
# ---------------------------------------------------------------------------

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
    """The explicit second button press that lets a selected strategy
    actually trade today -- selecting only opens its panel."""
    strategy = _require_strategy(key)
    if strategy["kind"] == "uploaded" and strategy["review_status"] != "approved":
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


# ---------------------------------------------------------------------------
# Per-strategy screener / positions
# ---------------------------------------------------------------------------

@router.get("/screener")
def get_screener(strategy_key: str = TOP_10_STRATEGY_KEY):
    session = market_clock.get_today_session()
    trading_date = str(session.trading_date)
    day = repo.get_day(trading_date, strategy_key) or {}
    settings = repo.get_settings()
    candidates = repo.list_candidates(trading_date, strategy_key)

    progress_pct = None
    if day.get("screener_started_at") and session.has_session:
        started = session.open_at
        window = settings["screener_window_minutes"]
        elapsed_minutes = max(0.0, (session.now - started).total_seconds() / 60.0)
        progress_pct = round(min(100.0, (elapsed_minutes / window) * 100), 1) if window else 100.0

    return {
        "trading_date": trading_date,
        "strategy_key": strategy_key,
        "phase": day.get("phase", "inactive"),
        "window_minutes": settings["screener_window_minutes"],
        "progress_pct": progress_pct,
        "candidates": candidates,
    }


@router.get("/positions")
def get_positions(strategy_key: str = TOP_10_STRATEGY_KEY, bucket: Optional[str] = None, status: Optional[str] = None):
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


def _map_action_error(exc: engine.ManualActionError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/positions/{position_id}/sell")
def sell_position(position_id: int):
    try:
        return engine.manual_sell(position_id)
    except engine.ManualActionError as exc:
        raise _map_action_error(exc) from exc


@router.post("/positions/{position_id}/promote")
def promote_position(position_id: int):
    """Move a paper position to real money -- gated on ALPACA_LIVE_EXECUTE
    only (see engine.manual_promote's docstring for why this differs from
    the scheduler's own automatic promotion gate). Only ever reachable for a
    Top 10 position in practice -- uploaded strategies never open a position
    a human couldn't have promoted, but they also never auto-promote one
    themselves (see uploads.py)."""
    try:
        return engine.manual_promote(position_id)
    except engine.ManualActionError as exc:
        raise _map_action_error(exc) from exc


@router.post("/positions/{position_id}/demote")
def demote_position(position_id: int):
    try:
        return engine.manual_demote(position_id)
    except engine.ManualActionError as exc:
        raise _map_action_error(exc) from exc
