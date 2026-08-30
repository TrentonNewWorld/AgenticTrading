"""The Crypto dashboard's per-interval tick -- runs an approved uploaded
crypto strategy's ``decide_crypto()`` and rebalances its paper (or, if
explicitly armed, real) positions to match. Mirrors domain/futures/
engine.py's and domain/forex/engine.py's shape, carrying forward both
lessons from the Futures build (quotes include prev_close; opens are capped
against actual available cash) from the start. ``qty`` is a float throughout
-- see domain/crypto/sandbox.py's docstring for why fractional coin
quantities are a requirement here, not an edge case.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dashboard.backend.domain.crypto import repository as repo
from dashboard.backend.domain.crypto.sandbox import run_decide_crypto
from dashboard.backend.infrastructure.market_data.alpaca_crypto import (
    CRYPTO_UNIVERSE,
    MarketDataUnavailableError,
    get_crypto_daily_bars,
    get_crypto_quotes_batch,
)


def _enrich_quotes_with_closes(quotes: Dict[str, Dict[str, Any]]) -> None:
    """Mirror the backtester's ``quotes[sym]["closes"]`` on live ticks, so an
    activated indicator strategy (SMA cross, RSI, Donchian, ...) sees the
    same signal live as it did in its backtest. Trailing daily closes,
    oldest -> newest, ending at the live price. Best-effort per symbol: a
    history fetch failing leaves that symbol's closes as just [price], which
    indicator strategies read as "not enough history, do nothing" -- the
    same fail-safe as early backtest days."""
    from datetime import date

    from dashboard.backend.domain.crypto.backtester import CLOSES_LOOKBACK

    start = (date.today() - timedelta(days=CLOSES_LOOKBACK * 2)).isoformat()
    end = date.today().isoformat()
    for sym, q in quotes.items():
        closes: List[float] = []
        try:
            bars = get_crypto_daily_bars(sym, start, end)
            closes = [b["c"] for b in bars]
        except (MarketDataUnavailableError, Exception):
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
    return os.getenv("ALPACA_PAPER_CRYPTO_EXECUTE", "false").strip().lower() in {"1", "true", "yes", "on"}


def _submit_real_order(symbol: str, side: str, qty: float) -> bool:
    try:
        from dashboard.backend.infrastructure.brokers.alpaca_paper_crypto import AlpacaPaperCryptoClient

        client = AlpacaPaperCryptoClient()
        client.submit_crypto_order(symbol=symbol, side=side, qty=qty)
        return True
    except Exception as exc:
        print(f"crypto engine: real Alpaca crypto order failed for {symbol}: {exc}")
        return False


def tick_uploaded_strategy(
    trading_date: str, strategy_key: str, strategy_def: Dict[str, Any], session,
) -> Dict[str, Any]:
    if strategy_def["review_status"] != "approved":
        return {"phase": "not_approved"}

    day = repo.ensure_day(trading_date, strategy_key)
    last_run = day.get("screener_completed_at")
    interval = timedelta(minutes=strategy_def["interval_minutes"] or MIN_INTERVAL_MINUTES)
    if last_run:
        last_run_dt = datetime.fromisoformat(last_run)
        if session.now < last_run_dt + interval:
            return {"phase": "waiting"}

    open_positions = repo.list_positions(trading_date, strategy_key, bucket="paper", status="open")
    quotes = get_crypto_quotes_batch(CRYPTO_UNIVERSE)
    if not quotes:
        print(f"crypto engine: no quotes available for {strategy_key}, skipping this tick")
        return {"phase": "error"}
    _enrich_quotes_with_closes(quotes)

    committed = sum(p["shares"] * p["entry_price"] for p in open_positions)
    # The shared Alpaca paper account's real balance sizes new positions
    # instead of the flat $1,000 simulated wallet, once connected -- see
    # domain/wallets.py::get_broker_cash_basis. Unconnected, unchanged.
    from dashboard.backend.domain.wallets import get_broker_cash_basis
    total_wallet = get_broker_cash_basis("crypto") or 1000.0
    cash = max(0.0, total_wallet - committed)
    account = {"cash": cash, "equity": cash + committed}
    intents = run_decide_crypto(
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


class ManualActionError(ValueError):
    """A manual sell request that can't be carried out as asked -- the API
    layer maps this straight to a 400."""


def _current_price_for(symbol: str) -> Optional[float]:
    try:
        quotes = get_crypto_quotes_batch([symbol])
    except MarketDataUnavailableError:
        return None
    quote = quotes.get(symbol)
    return quote["price"] if quote else None


def manual_sell(position_id: int) -> Dict[str, Any]:
    """A human clicked Sell on the Crypto Manual page. Paper-only unless
    ALPACA_PAPER_CRYPTO_EXECUTE is armed -- matches every other dashboard's
    manual_sell posture exactly, even though this particular broker
    connection is the one genuinely verified working end to end."""
    position = repo.get_position(position_id)
    if position is None:
        raise ManualActionError(f"no position {position_id}")
    if position["status"] != "open":
        raise ManualActionError(f"position {position_id} is already {position['status']}")

    current_price = _current_price_for(position["symbol"])
    if current_price is None:
        raise ManualActionError(f"no live quote for {position['symbol']} right now")

    if _execute_enabled():
        ok = _submit_real_order(position["symbol"], "sell", float(position["shares"]))
        if not ok:
            raise ManualActionError(f"real sell order failed for {position['symbol']} -- position left open")

    repo.close_position(position_id, exit_price=current_price, close_reason="manual_sell")
    return repo.get_position(position_id)
