"""Crypto Competition + Live Trading Leaderboards. Sibling router, mirrors
api/routers/futures_leaderboard.py and api/routers/forex_leaderboard.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from dashboard.backend.domain.crypto.leaderboard_service import get_leaderboard

router = APIRouter(prefix="/v1/crypto/leaderboard", tags=["crypto-leaderboard"])


@router.get("")
def leaderboard(period: str = "contest", refresh: bool = False):
    return get_leaderboard(period, force_refresh=refresh)
