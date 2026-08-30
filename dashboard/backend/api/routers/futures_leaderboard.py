"""Futures Competition + Live Trading Leaderboards. Sibling router,
mirrors api/routers/options_leaderboard.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from dashboard.backend.domain.futures.leaderboard_service import get_leaderboard

router = APIRouter(prefix="/v1/futures/leaderboard", tags=["futures-leaderboard"])


@router.get("")
def leaderboard(period: str = "contest", refresh: bool = False):
    return get_leaderboard(period, force_refresh=refresh)
