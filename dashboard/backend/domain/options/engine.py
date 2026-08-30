"""The Options dashboard's per-interval tick -- runs an approved uploaded
options strategy's ``decide_options()`` and rebalances its paper (or, if
explicitly armed, real) positions to match.

Sub-phase 5 of the Options-dashboard plan. Modeled on ``domain/manual10/
uploads.py``'s ``tick_uploaded_strategy`` (a simple per-interval tick, no
opening-range screener phase) rather than ``manual10/engine.py``'s 5-phase
Top-10 state machine -- Options strategies are uploaded/full-contract-level
from day one, there is no built-in Top-10-equivalent screener for options to
warrant the extra phase machinery. Reuses ``domain/manual10/market_clock.py``
unchanged: options trade on the same NYSE equity session Alpaca already
tracks there.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dashboard.backend.domain.options import repository as repo
from dashboard.backend.domain.options.sandbox import run_decide_options
from dashboard.backend.infrastructure.brokers.alpaca_paper_options import OptionLeg
from dashboard.backend.infrastructure.market_data.alpaca_options import (
    MarketDataUnavailableError,
    get_option_chain_snapshot,
)

#: Options strategies don't declare their own universe (no upload-time field
#: for it, matching manual10/uploads.py's own hardcoded DJIA_30 default) --
#: this is a small, deliberately liquid set so every chain fetch has real
#: bid/ask depth. A future per-strategy universe is a straightforward
#: addition (a new manual10_strategies column) but not needed for v1.
DEFAULT_OPTIONS_UNIVERSE = ["SPY", "QQQ", "AAPL", "MSFT"]

MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 240


def _build_chain(underlyings: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    chain: Dict[str, List[Dict[str, Any]]] = {}
    for underlying in underlyings:
        try:
            snapshot = get_option_chain_snapshot(underlying)
        except MarketDataUnavailableError as exc:
            print(f"options engine: chain fetch failed for {underlying}: {exc}")
            continue
        chain[underlying] = list(snapshot.values())
    return chain


def _positions_payload(open_positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "symbol": p["symbol"],
            "underlying": p.get("underlying_symbol"),
            "strike": p.get("strike_price"),
            "expiration": p.get("expiration_date"),
            "right": p.get("option_right"),
            "qty": p["shares"],
            "leg_role": p.get("leg_role") or "single",
        }
        for p in open_positions
    ]


def _leg_mid_price(chain: Dict[str, List[Dict[str, Any]]], symbol: str) -> Optional[float]:
    for contracts in chain.values():
        for contract in contracts:
            if contract.get("symbol") == symbol:
                bid, ask = contract.get("bid"), contract.get("ask")
                if bid and ask:
                    return round((bid + ask) / 2, 2)
                return contract.get("last")
    return None


def _underlying_for_symbol(chain: Dict[str, List[Dict[str, Any]]], symbol: str) -> Optional[str]:
    for underlying, contracts in chain.items():
        for contract in contracts:
            if contract.get("symbol") == symbol:
                return underlying
    return None


def tick_uploaded_strategy(
    trading_date: str, strategy_key: str, strategy_def: Dict[str, Any], session,
) -> Dict[str, Any]:
    """Run this approved options strategy's decide_options() on its own
    fixed interval and rebalance its paper (or armed-real) positions.
    Deliberately paper-only unless ALPACA_PAPER_OPTIONS_EXECUTE is set --
    matches manual10 uploads.py's own "never auto-promotes to real money"
    posture: nothing here decides on its own to spend real money on code a
    human hasn't fully vetted."""
    if strategy_def["review_status"] != "approved":
        return {"phase": "not_approved"}

    day = repo.ensure_day(trading_date, strategy_key)
    last_run = day.get("screener_completed_at")  # reused as "last tick" timestamp, matching uploads.py
    interval = timedelta(minutes=strategy_def["interval_minutes"] or MIN_INTERVAL_MINUTES)
    if last_run:
        last_run_dt = datetime.fromisoformat(last_run)
        if session.now < last_run_dt + interval:
            return {"phase": "waiting"}

    open_positions = repo.list_positions(trading_date, strategy_key, bucket="paper", status="open")
    chain = _build_chain(DEFAULT_OPTIONS_UNIVERSE)
    if not chain:
        print(f"options engine: no chain data available for {strategy_key}, skipping this tick")
        return {"phase": "error"}

    # The shared Alpaca paper account's real balance, once connected --
    # see domain/wallets.py::get_broker_cash_basis. Unconnected, falls back
    # to the nominal paper capital, matching manual10 uploads.py's
    # INITIAL_CAPITAL posture (this tick has no committed-capital tracking
    # of its own to net against, unlike Futures/Forex/Crypto's engines).
    from dashboard.backend.domain.wallets import get_broker_cash_basis
    nominal_cash = get_broker_cash_basis("options") or 10000.0
    account = {"cash": nominal_cash, "equity": nominal_cash}
    intents = run_decide_options(
        strategy_def["code"],
        as_of=trading_date,
        positions=_positions_payload(open_positions),
        chain=chain,
        account=account,
        timeout=10,
    )

    execute = os.getenv("ALPACA_PAPER_OPTIONS_EXECUTE", "false").strip().lower() in {"1", "true", "yes", "on"}
    client = None
    if execute:
        from dashboard.backend.infrastructure.brokers.alpaca_paper_options import AlpacaPaperOptionsClient
        try:
            client = AlpacaPaperOptionsClient()
        except Exception as exc:
            print(f"options engine: could not build paper client, falling back to simulated fills: {exc}")
            client = None

    # Group "open" intents by underlying -- everything a strategy returns
    # for one underlying in one cycle is submitted together (one multi-leg
    # order if 2+ legs, one simple order if 1), per the sandbox contract's
    # own documented grouping rule.
    opens_by_underlying: Dict[str, List[Dict[str, Any]]] = {}
    closes: List[Dict[str, Any]] = []
    for intent in intents:
        if intent["action"] == "close":
            closes.append(intent)
            continue
        underlying = _underlying_for_symbol(chain, intent["symbol"]) or intent["symbol"]
        opens_by_underlying.setdefault(underlying, []).append(intent)

    orders_placed = 0
    open_by_symbol = {p["symbol"]: p for p in open_positions}

    for underlying, group in opens_by_underlying.items():
        leg_group_id = f"{strategy_key}_{trading_date}_{underlying}_{session.now.strftime('%H%M%S')}"
        legs = [OptionLeg(symbol=i["symbol"], side=i["side"]) for i in group]
        net_price = sum(
            (_leg_mid_price(chain, leg.symbol) or 0) * (1 if leg.side == "buy" else -1)
            for leg in legs
        )
        limit_price = max(round(abs(net_price), 2), 0.01)

        if execute and client is not None:
            result = client.submit_option_order(legs, limit_price=limit_price)
            if result is None:
                continue

        for intent in group:
            entry_price = _leg_mid_price(chain, intent["symbol"]) or 0.0
            repo.open_position(
                trading_date=trading_date, strategy_key=strategy_key, symbol=intent["symbol"],
                bucket="paper", shares=float(intent["qty"]), entry_price=entry_price,
                underlying_symbol=underlying, leg_group_id=leg_group_id, leg_role=intent["leg_role"],
            )
            orders_placed += 1

    for intent in closes:
        matching = open_by_symbol.get(intent["symbol"])
        if not matching:
            continue
        exit_price = _leg_mid_price(chain, intent["symbol"]) or matching["entry_price"]
        if execute and client is not None:
            client.submit_option_order(
                [OptionLeg(symbol=intent["symbol"], side="sell" if intent["side"] == "buy" else "buy")],
                limit_price=max(round(exit_price, 2), 0.01),
            )
        repo.close_position(matching["id"], exit_price=exit_price, close_reason="strategy_close")

    repo.update_day(trading_date, strategy_key, phase="holding", screener_completed_at=session.now.isoformat())
    return {"phase": "holding", "orders": orders_placed, "closes": len(closes)}


class ManualActionError(ValueError):
    """A manual sell request that can't be carried out as asked -- the API
    layer maps this straight to a 400."""


def _current_price_for(symbol: str, leg_role: Optional[str]) -> Optional[float]:
    """A stock leg prices from an ordinary equity quote; an option leg
    prices from the live chain snapshot's own bid/ask mid -- the same
    dual-source logic domain.options.views._enrich uses for display."""
    if leg_role == "stock":
        from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient

        try:
            quotes = AlpacaPaperTradingClient().get_quotes([symbol])
        except Exception as exc:
            print(f"options engine: equity quote fetch failed for {symbol}: {exc}")
            return None
        return quotes.get(symbol.upper())

    from dashboard.backend.infrastructure.market_data.alpaca_options import parse_occ_symbol

    try:
        underlying = parse_occ_symbol(symbol)["underlying"]
    except Exception:
        return None
    try:
        snapshot = get_option_chain_snapshot(underlying)
    except MarketDataUnavailableError as exc:
        print(f"options engine: chain fetch failed for {underlying}: {exc}")
        return None
    contract = snapshot.get(symbol)
    if not contract:
        return None
    return _leg_mid_price({underlying: [contract]}, symbol)


def manual_sell(position_id: int) -> Dict[str, Any]:
    """A human clicked Sell on the Options Manual page. Deliberately
    paper-only -- see module docstring; there is no real-money execute path
    for Options in this phase, matching manual10/uploads.py's own
    "uploaded strategies never auto-promote to real money" posture, taken
    one step further here (no promote button exists for Options at all yet)."""
    position = repo.get_position(position_id)
    if position is None:
        raise ManualActionError(f"no position {position_id}")
    if position["status"] != "open":
        raise ManualActionError(f"position {position_id} is already {position['status']}")

    current_price = _current_price_for(position["symbol"], position.get("leg_role"))
    if current_price is None:
        raise ManualActionError(f"no live quote for {position['symbol']} right now")

    execute = os.getenv("ALPACA_PAPER_OPTIONS_EXECUTE", "false").strip().lower() in {"1", "true", "yes", "on"}
    if execute:
        from dashboard.backend.infrastructure.brokers.alpaca_paper_options import AlpacaPaperOptionsClient

        try:
            client = AlpacaPaperOptionsClient()
            client.submit_option_order(
                [OptionLeg(symbol=position["symbol"], side="sell")],
                limit_price=max(round(current_price, 2), 0.01),
            )
        except Exception as exc:
            raise ManualActionError(f"sell order failed: {exc}") from exc

    repo.close_position(position_id, exit_price=current_price, close_reason="manual_sell")
    return repo.get_position(position_id)
