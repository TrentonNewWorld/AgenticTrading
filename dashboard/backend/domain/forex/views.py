"""Read-only, display-oriented views over the Forex Manual page's ledger.
Mirrors domain/futures/views.py exactly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dashboard.backend.domain.forex import repository as repo
from dashboard.backend.infrastructure.market_data.yahoo_forex import get_forex_quotes_batch


def _enrich(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    symbols = sorted({p["symbol"] for p in positions if p["status"] == "open"})
    try:
        quotes = get_forex_quotes_batch(symbols) if symbols else {}
    except Exception as exc:
        print(f"forex views: quote fetch failed: {exc}")
        quotes = {}

    ten_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    enriched: List[Dict[str, Any]] = []
    for p in positions:
        if p["status"] == "open":
            quote = quotes.get(p["symbol"])
            current_price = quote["price"] if quote else None
        else:
            current_price = p.get("exit_price")
        if current_price is None:
            current_price = p["entry_price"]

        entry = p["entry_price"] or 0.0
        change_pct = ((current_price - entry) / entry * 100) if entry else 0.0
        cost_basis = entry * p["shares"]
        current_value = current_price * p["shares"]
        enriched.append({
            **p,
            "current_price": round(current_price, 4),
            "change_pct": round(change_pct, 2),
            "cost_basis": round(cost_basis, 2),
            "current_value": round(current_value, 2),
            "unrealized_pnl": round(current_value - cost_basis, 2),
            "price_10min_ago": repo.price_snapshot_near(
                p["trading_date"], p["strategy_key"], p["symbol"], ten_min_ago,
            ),
        })
    return enriched


def enrich_positions(
    trading_date: str, strategy_key: str, *, bucket: Optional[str] = None, status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    positions = repo.list_positions(trading_date, strategy_key, bucket=bucket, status=status)
    return _enrich(positions)


def strategy_status(trading_date: str, strategy_key: str) -> Dict[str, Any]:
    day = repo.get_day(trading_date, strategy_key) or {}
    activation = repo.get_activation(trading_date, strategy_key) or {}
    return {
        "strategy_key": strategy_key,
        "phase": day.get("phase", "inactive"),
        "selected": bool(activation.get("selected")),
        "activated": bool(activation.get("activated")),
        "wallet_reset_amount": day.get("wallet_reset_amount"),
        "realized_pnl_today": day.get("realized_pnl"),
        "result_today": day.get("result"),
    }


def wallet_summary(trading_date: str) -> Dict[str, Any]:
    all_open: List[Dict[str, Any]] = []
    realized_today = 0.0
    any_result = False
    for activation in repo.list_activations(trading_date):
        if not activation["activated"]:
            continue
        strategy_key = activation["strategy_key"]
        all_open.extend(repo.list_positions(trading_date, strategy_key, status="open"))
        day = repo.get_day(trading_date, strategy_key)
        if day and day.get("realized_pnl") is not None:
            realized_today += day["realized_pnl"]
            any_result = True

    enriched_open = _enrich(all_open)
    open_value = sum(p["current_value"] for p in enriched_open)
    unrealized_pnl = sum(p["unrealized_pnl"] for p in enriched_open)
    return {
        "trading_date": trading_date,
        "open_positions_value": round(open_value, 2),
        "unrealized_pnl_today": round(unrealized_pnl, 2),
        "realized_pnl_today": round(realized_today, 2) if any_result else None,
    }


def calendar(limit: int = 90) -> List[Dict[str, Any]]:
    return [
        {
            "trading_date": d["trading_date"],
            "strategy_key": d["strategy_key"],
            "phase": d["phase"],
            "result": d["result"],
            "realized_pnl": d["realized_pnl"],
        }
        for d in repo.list_days(limit=limit)
    ]
