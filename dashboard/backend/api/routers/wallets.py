"""Real-broker wallet balances -- one route each dashboard's Manual page
calls for its own wallet card(s), and one Home page calls for the combined
real-money total. Thin wrapper: all the resolution/dedup logic lives in
domain/wallets.py.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from dashboard.backend.api.auth import get_current_user_optional
from dashboard.backend.domain.wallets import ASSET_CLASS_LABELS, get_dashboard_wallets, get_portfolio_summary

router = APIRouter(prefix="/v1/wallets", tags=["wallets"])


@router.get("/portfolio-summary")
def portfolio_summary(current_user: Optional[dict] = Depends(get_current_user_optional)):
    """Home page's combined real-money total across every connected broker,
    deduped so an account shared by multiple dashboards (Alpaca paper across
    Stocks/Options/Crypto) is counted once, not once per dashboard."""
    user_id = current_user["id"] if current_user else None
    return get_portfolio_summary(user_id)


@router.get("/{asset_class}")
def dashboard_wallets(asset_class: str, current_user: Optional[dict] = Depends(get_current_user_optional)):
    if asset_class not in ASSET_CLASS_LABELS:
        raise HTTPException(status_code=404, detail=f"unknown asset class: {asset_class}")
    user_id = current_user["id"] if current_user else None
    return {"asset_class": asset_class, "wallets": get_dashboard_wallets(asset_class, user_id)}
