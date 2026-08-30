"""The Futures dashboard's per-interval tick -- runs an approved uploaded
futures strategy's ``decide_futures()`` and rebalances its paper (or, if
explicitly armed, real) positions to match.

Modeled on domain/options/engine.py's simpler per-interval tick (no opening-
range screener phase, per the user's own "upload-only, like Options"
decision for the Futures Manual page) -- simpler still, since futures
positions are single-instrument: no leg grouping, no chain fetch, just a flat
quote per symbol from free Yahoo Finance data
(infrastructure/market_data/yahoo_futures.py). Reuses domain/futures/
market_clock.py, not domain/manual10/market_clock.py -- futures trade nearly
24/5, there is no NYSE session to key off.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dashboard.backend.domain.futures import market_clock, repository as repo
from dashboard.backend.domain.futures.sandbox import run_decide_futures
from dashboard.backend.infrastructure.market_data.yahoo_futures import (
    FUTURES_UNIVERSE,
    MarketDataUnavailableError,
    get_futures_daily_bars,
    get_futures_quotes_batch,
)


def _enrich_quotes_with_closes(quotes):
    """Mirror the backtester's quotes[sym]["closes"] on live ticks -- see
    domain/crypto/engine.py's twin for the full rationale. Best-effort: a
    failed history fetch leaves closes as [price], which indicator
    strategies read as "not enough history, do nothing"."""
    from datetime import date

    from dashboard.backend.domain.futures.backtester import CLOSES_LOOKBACK

    start = (date.today() - timedelta(days=CLOSES_LOOKBACK * 2)).isoformat()
    end = (date.today() + timedelta(days=1)).isoformat()  # yfinance end-exclusive
    for sym, q in quotes.items():
        try:
            closes = [b["c"] for b in get_futures_daily_bars(sym, start, end)]
        except Exception:
            closes = []
        price = q.get("price")
        if price is not None:
            closes = closes + [float(price)]
        q["closes"] = closes[-CLOSES_LOOKBACK:]

MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 240


def _positions_payload(open_positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"symbol": p["symbol"], "qty": p["shares"]} for p in open_positions]


def _execute_enabled() -> bool:
    return os.getenv("TRADOVATE_PAPER_EXECUTE", "false").strip().lower() in {"1", "true", "yes", "on"}


def _tradovate_credentials():
    """A Connections-saved Tradovate credential, if any -- not user-scoped
    (see connection_store.get_credentials_any_user's docstring: Futures
    strategies have no owner to begin with). Falls through to None so the
    caller's own credentials_from_env() fallback still applies, matching
    every other broker client's "Connections store first, then env/file"
    convention in this repo."""
    from dashboard.backend.domain.connections.repository import connection_store

    saved = connection_store.get_credentials_any_user("tradovate")
    if not saved:
        return None
    from dashboard.backend.infrastructure.brokers.tradovate_paper import TradovateCredentials

    try:
        return TradovateCredentials(
            name=saved["username"], password=saved["password"], cid=saved["cid"],
            sec=saved["sec"], account_spec=saved["account_spec"], account_id=int(saved["account_id"]),
        )
    except (KeyError, ValueError):
        return None


def _submit_real_order(symbol: str, side: str, qty: int) -> bool:
    """Returns True on success. Any failure is caught and logged rather than
    raised -- a broker-side hiccup costs this cycle's real order, not the
    whole tick (the simulated paper position still opens/closes either way,
    since the wallet is fully simulated regardless of TRADOVATE_PAPER_EXECUTE
    -- that flag only controls whether a *real* order also goes out)."""
    try:
        from dashboard.backend.infrastructure.brokers.tradovate_paper import (
            TradovateOrderError,
            TradovatePaperClient,
        )

        client = TradovatePaperClient(credentials=_tradovate_credentials())
        client.place_order(symbol=symbol, side=side, qty=qty)
        return True
    except Exception as exc:
        print(f"futures engine: real Tradovate order failed for {symbol}: {exc}")
        return False


def tick_uploaded_strategy(
    trading_date: str, strategy_key: str, strategy_def: Dict[str, Any], session,
) -> Dict[str, Any]:
    if strategy_def["review_status"] != "approved":
        return {"phase": "not_approved"}

    day = repo.ensure_day(trading_date, strategy_key)
    last_run = day.get("screener_completed_at")  # reused as "last tick" timestamp, matching options/manual10 uploads.py
    interval = timedelta(minutes=strategy_def["interval_minutes"] or MIN_INTERVAL_MINUTES)
    if last_run:
        last_run_dt = datetime.fromisoformat(last_run)
        if session.now < last_run_dt + interval:
            return {"phase": "waiting"}

    open_positions = repo.list_positions(trading_date, strategy_key, bucket="paper", status="open")
    quotes = get_futures_quotes_batch(FUTURES_UNIVERSE)
    if not quotes:
        print(f"futures engine: no quotes available for {strategy_key}, skipping this tick")
        return {"phase": "error"}
    _enrich_quotes_with_closes(quotes)

    # A real futures contract's notional (price * qty) usually dwarfs the
    # $1,000 simulated wallet this dashboard uses everywhere, so unlike a
    # flat nominal constant, cash here is actually reduced by what's already
    # committed to open positions -- a live-verification run against real
    # data caught a version of this that always reported cash=1000 hardcoded
    # regardless of open positions, which combined with backtester.py's own
    # now-fixed missing cash check to produce impossible triple-digit annual
    # returns (see that module's fix for the full story).
    committed = sum(p["shares"] * p["entry_price"] for p in open_positions)
    # A connected Tradovate account's real cash balance sizes new positions
    # instead of the flat $1,000 simulated wallet, once one is connected --
    # see domain/wallets.py::get_broker_cash_basis for the fallback/scope
    # reasoning. Unconnected (the zero-setup default), nothing changes.
    from dashboard.backend.domain.wallets import get_broker_cash_basis
    total_wallet = get_broker_cash_basis("futures") or 1000.0
    cash = max(0.0, total_wallet - committed)
    account = {"cash": cash, "equity": cash + committed}
    intents = run_decide_futures(
        strategy_def["code"],
        as_of=trading_date,
        positions=_positions_payload(open_positions),
        quotes=quotes,
        account=account,
        timeout=10,
    )

    execute = _execute_enabled()
    open_by_symbol = {p["symbol"]: p for p in open_positions}
    orders_placed = 0
    closes = 0

    for intent in intents:
        quote = quotes.get(intent["symbol"])
        price = quote["price"] if quote else None
        if intent["action"] == "open":
            if price is None:
                continue
            cost = price * intent["qty"]
            if cost > cash:
                continue  # would exceed the wallet -- see the cash calc above
            cash -= cost
            if execute:
                _submit_real_order(intent["symbol"], intent["side"], intent["qty"])
            repo.open_position(
                trading_date=trading_date, strategy_key=strategy_key, symbol=intent["symbol"],
                bucket="paper", shares=float(intent["qty"]), entry_price=price,
            )
            orders_placed += 1
        else:
            matching = open_by_symbol.get(intent["symbol"])
            if not matching:
                continue
            exit_price = price if price is not None else matching["entry_price"]
            if execute:
                _submit_real_order(intent["symbol"], "sell" if intent["side"] == "buy" else "buy", intent["qty"])
            repo.close_position(matching["id"], exit_price=exit_price, close_reason="strategy_close")
            closes += 1

    repo.update_day(trading_date, strategy_key, phase="holding", screener_completed_at=session.now.isoformat())
    return {"phase": "holding", "orders": orders_placed, "closes": closes}


def tick(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Advance every activated Futures strategy by one step -- the
    aggregator domain/futures/scheduler.py's daemon calls on an interval.
    Mirrors domain/manual10/engine.py::tick() exactly, minus the builtin-
    strategy branch (Futures is upload-only, see this module's docstring).
    Without this loop actually running somewhere, activating a strategy did
    nothing at all: tick_uploaded_strategy() existed but nothing ever called
    it, so no Futures position -- paper or real -- could ever open."""
    session = market_clock.get_today_session()
    trading_date = str(session.trading_date)
    results: Dict[str, Any] = {}
    for activation in repo.list_activations(trading_date):
        if not activation["activated"]:
            continue
        strategy_key = activation["strategy_key"]
        strategy_def = repo.get_strategy_def(strategy_key)
        if strategy_def is None:
            continue
        results[strategy_key] = tick_uploaded_strategy(trading_date, strategy_key, strategy_def, session)
    return {"trading_date": trading_date, "strategies": results}


class ManualActionError(ValueError):
    """A manual sell request that can't be carried out as asked -- the API
    layer maps this straight to a 400."""


def _current_price_for(symbol: str) -> Optional[float]:
    try:
        quotes = get_futures_quotes_batch([symbol])
    except MarketDataUnavailableError:
        return None
    quote = quotes.get(symbol)
    return quote["price"] if quote else None


def manual_sell(position_id: int) -> Dict[str, Any]:
    """A human clicked Sell on the Futures Manual page. Paper-only unless
    TRADOVATE_PAPER_EXECUTE is armed -- matches domain/options/engine.py's
    manual_sell posture exactly."""
    position = repo.get_position(position_id)
    if position is None:
        raise ManualActionError(f"no position {position_id}")
    if position["status"] != "open":
        raise ManualActionError(f"position {position_id} is already {position['status']}")

    current_price = _current_price_for(position["symbol"])
    if current_price is None:
        raise ManualActionError(f"no live quote for {position['symbol']} right now")

    if _execute_enabled():
        ok = _submit_real_order(position["symbol"], "sell", int(position["shares"]))
        if not ok:
            raise ManualActionError(f"real sell order failed for {position['symbol']} -- position left open")

    repo.close_position(position_id, exit_price=current_price, close_reason="manual_sell")
    return repo.get_position(position_id)
