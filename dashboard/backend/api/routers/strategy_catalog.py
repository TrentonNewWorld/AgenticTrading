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

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dashboard.backend.domain.leaderboard.catalog import get_strategy_catalog
from dashboard.backend.execution import alpaca_live_service, alpaca_paper_service

router = APIRouter(prefix="/v1/strategy-catalog", tags=["strategy-catalog"])


@router.get("")
def list_catalog(refresh: bool = False):
    return get_strategy_catalog(force_refresh=refresh)


class RunRequestBody(BaseModel):
    #: Always defaults to a dry run. A client CAN pass `dry_run: false`, but
    #: that alone is not enough to place a real order -- both services also
    #: require their own ALPACA_*_EXECUTE env var to be armed server-side.
    dry_run: bool = True
    symbols: Optional[list[str]] = None


@router.post("/{key}/paper")
async def run_paper(key: str, body: RunRequestBody):
    try:
        return await alpaca_paper_service.run_paper_for_strategy(
            strategy_key=key, symbols=body.symbols, dry_run=body.dry_run,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{key}/live")
async def run_live(key: str, body: RunRequestBody):
    try:
        return await alpaca_live_service.run_live_for_strategy(
            strategy_key=key, symbols=body.symbols, dry_run=body.dry_run,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
