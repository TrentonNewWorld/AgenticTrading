"""Mission Control overview: real-money and paper wallet balances and
holdings side by side, in one response, for the dedicated dashboard page.

Deliberately read-only. Placing an order is out of scope for this router --
see ``dashboard.backend.execution.alpaca_live_service`` for the risk-gated
live-trading path.

Regression note: this router used to construct both Alpaca clients with no
``user_id`` at all, so it could only ever show the server's own env-var
account (``ALPACA_API_KEY``/``ALPACA_LIVE_API_KEY``) -- a signed-in user's
own Connections-saved key was silently ignored, so connecting your own key
there never changed what this page showed (caught live: a real user
connected their Alpaca key and the wallet amount never updated). Both
clients already support a ``user_id`` param that prefers a signed-in user's
Connections-saved key over the env vars (``infrastructure/brokers/
credentials.py``); this router just wasn't passing it through.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends

from dashboard.backend.api.auth import get_current_user_optional
from dashboard.backend.execution.alpaca_live_service import execute_enabled, max_order_usd
from dashboard.backend.infrastructure.brokers.alpaca_live import (
    AlpacaLiveCredentialsError,
    AlpacaLiveTradingClient,
)
from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient

router = APIRouter(prefix="/v1/mission-control", tags=["mission-control"])


def _paper_snapshot(user_id: Optional[int]) -> dict:
    try:
        client = AlpacaPaperTradingClient(user_id=user_id)
    except Exception as e:
        print(f"⚠️ Mission Control: paper account unavailable: {e}")
        return {"configured": False, "error": "Paper account not configured", "account": None, "positions": []}

    account = client.get_account()
    positions = client.get_positions()
    return {
        "configured": account is not None,
        "error": None if account is not None else "Failed to fetch paper account",
        "account": account,
        "positions": [
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "avg_entry_price": p.avg_fill_price,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "unrealized_pl": p.unrealized_pl,
                "unrealized_plpc": p.unrealized_plpc,
                "side": p.side,
            }
            for p in positions
        ],
    }


def _live_snapshot(user_id: Optional[int]) -> dict:
    try:
        client = AlpacaLiveTradingClient(user_id=user_id)
    except AlpacaLiveCredentialsError as e:
        print(f"⚠️ Mission Control: live account not configured: {e}")
        return {"configured": False, "error": "Live account not configured", "account": None, "positions": []}

    account = client.get_account()
    positions = client.get_positions_detailed()
    return {
        "configured": account is not None,
        "error": None if account is not None else "Failed to fetch live account",
        "account": account,
        "positions": positions,
    }


@router.get("/overview")
def get_overview(current_user: Optional[dict] = Depends(get_current_user_optional)):
    """Everything the Mission Control page needs in one call: both wallets,
    both holdings lists, and whether live execution is actually armed.
    Signed in, this prefers the caller's own Connections-saved Alpaca keys
    over the server's env vars; signed out, it falls back to the env vars
    exactly as before."""
    user_id = current_user["id"] if current_user else None
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "paper": _paper_snapshot(user_id),
        "live": _live_snapshot(user_id),
        "live_execute_enabled": execute_enabled(),
        "live_max_order_usd": max_order_usd(),
    }
