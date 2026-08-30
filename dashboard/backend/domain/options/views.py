"""Read-only, display-oriented views over the Options Manual page's ledger.

Sub-phase 10 of the Options-dashboard plan (the Manual page's HTTP surface
needed a backend to call, which Sub-phase 5 didn't build a router for --
only the tick/engine logic). Mirrors ``domain/manual10/views.py``'s shape,
with one real difference: a position here can be an **option** leg (priced
from the live chain snapshot, not an equity quote) or a **stock** leg
(priced from an ordinary equity quote) -- ``_current_price_for`` picks the
right source per position.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dashboard.backend.domain.options import repository as repo
from dashboard.backend.infrastructure.market_data.alpaca_options import (
    MarketDataUnavailableError,
    OptionSymbolError,
    get_option_chain_snapshot,
    parse_occ_symbol,
)


def _equity_quotes_for(symbols: List[str]) -> Dict[str, float]:
    if not symbols:
        return {}
    from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient

    try:
        return AlpacaPaperTradingClient().get_quotes(symbols)
    except Exception as exc:
        print(f"options views: equity quote fetch failed: {exc}")
        return {}


def _option_quotes_for(underlyings: List[str]) -> Dict[str, float]:
    quotes: Dict[str, float] = {}
    for underlying in set(underlyings):
        try:
            snapshot = get_option_chain_snapshot(underlying)
        except MarketDataUnavailableError as exc:
            print(f"options views: chain fetch failed for {underlying}: {exc}")
            continue
        for symbol, contract in snapshot.items():
            bid, ask = contract.get("bid"), contract.get("ask")
            if bid and ask:
                quotes[symbol] = round((bid + ask) / 2, 4)
            elif contract.get("last"):
                quotes[symbol] = contract["last"]
    return quotes


def _enrich(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stock_symbols, option_underlyings = [], []
    for p in positions:
        if p["status"] != "open":
            continue
        if p.get("leg_role") == "stock":
            stock_symbols.append(p["symbol"])
        else:
            try:
                parsed = parse_occ_symbol(p["symbol"])
                option_underlyings.append(parsed["underlying"])
            except OptionSymbolError:
                stock_symbols.append(p["symbol"])  # a "single" leg that's actually a plain equity

    quotes: Dict[str, float] = {}
    quotes.update(_equity_quotes_for(sorted(set(stock_symbols))))
    quotes.update(_option_quotes_for(option_underlyings))

    ten_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    enriched: List[Dict[str, Any]] = []
    for p in positions:
        if p["status"] == "open":
            current_price = quotes.get(p["symbol"])
        else:
            current_price = p.get("exit_price")
        if current_price is None:
            current_price = p["entry_price"]  # no quote available yet -- show flat rather than blank

        entry = p["entry_price"] or 0.0
        multiplier = 1 if p.get("leg_role") == "stock" else 100
        change_pct = ((current_price - entry) / entry * 100) if entry else 0.0
        cost_basis = entry * p["shares"] * multiplier
        current_value = current_price * p["shares"] * multiplier
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
