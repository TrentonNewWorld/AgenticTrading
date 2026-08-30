"""Options Competition + Live Trading Leaderboards.

Sub-phase 9 of the Options-dashboard plan. Sibling router to the stocks
``api/routers/leaderboard.py``, new URL space (``/v1/options/leaderboard``)
-- the underlying ``domain.options.leaderboard_service`` is a parallel
module, not a variant of the stocks one (see that module's docstring for why
a shared config/window/session-id-driven refactor was not attempted).
"""

from __future__ import annotations

from fastapi import APIRouter

from dashboard.backend.domain.options.leaderboard_service import get_leaderboard

router = APIRouter(prefix="/v1/options/leaderboard", tags=["options-leaderboard"])


@router.get("")
def leaderboard(period: str = "contest", refresh: bool = False):
    return get_leaderboard(period, force_refresh=refresh)
