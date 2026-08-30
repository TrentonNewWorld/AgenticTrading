"""Small pure helpers shared by ``alpaca_paper_service.py`` and
``alpaca_live_service.py`` for running a registered leaderboard strategy
(deterministic ``decide()``, never an LLM call) against a real Alpaca
account.

Lives in its own module rather than either service file because both
directions of a plain cross-import would cycle: ``alpaca_paper_service``
already imports ``risk_gate_orders`` from ``alpaca_live_service``, so
``alpaca_live_service`` importing anything back from
``alpaca_paper_service`` would form a loop.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from dashboard.backend.domain.leaderboard.strategies._signal_engine import DailyHistory

#: Enough for the longest lookback any registered strategy uses (Blue-Chip
#: Steady's 220-day pick, AI Hedge Fund's 252-day momentum) plus slack.
_LOOKBACK_CALENDAR_DAYS = 420


def fetch_daily_history(client: Any, symbols: List[str]) -> DailyHistory:
    """Fetch enough daily bars to cover every strategy's lookback, clamped to
    end = now - 1 day (Alpaca's Basic data plan rejects a query whose `end`
    falls inside the last ~15 minutes; a full calendar day of slack is used
    here since this only needs to run once per day, not intraday). Works with
    either the paper or the live broker client -- both expose ``.api_key``/
    ``.secret_key``, which is all this needs."""
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=_LOOKBACK_CALENDAR_DAYS)
    data_client = StockHistoricalDataClient(client.api_key, client.secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=symbols, timeframe=TimeFrame.Day, start=start, end=end,
        feed=DataFeed.SIP, adjustment="all",
    )
    bars = data_client.get_stock_bars(request)
    df = bars.df
    if df.empty:
        empty = pd.DataFrame()
        return DailyHistory(close=empty, open=empty, high=empty, low=empty, volume=empty)

    def _field(name: str) -> pd.DataFrame:
        cols = {}
        for sym, sub in df.groupby(level=0):
            sub = sub.droplevel(0)
            cols[sym] = sub[name]
        frame = pd.DataFrame(cols).sort_index()
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        return frame

    return DailyHistory(
        close=_field("close"), open=_field("open"), high=_field("high"),
        low=_field("low"), volume=_field("volume"),
    )


def capped_portfolio_value(
    strategy_key: str, account_cash: float, holdings: Dict[str, float], prices: Dict[str, float],
) -> float:
    """The dollar basis for sizing NEW purchases: never more than the
    strategy's own allocated capital (the Strategy Catalog's "Allocated"
    field, domain/leaderboard/real_trading.py::get_allocation), and never
    more than the account can actually afford. This is what makes Allocated a
    real cap rather than just a number the Real Trading ledger displays --
    before this, every strategy's real/paper run sized its buys against the
    WHOLE shared account's cash + holdings, regardless of what the user had
    set. Selling is still bounded by the account's real current holdings
    regardless (see the caller) -- one shared broker account has no way to
    know which existing shares "belong" to which strategy (see
    real_trading.py's own module docstring); this cap only limits how much
    new buying a strategy's target weights get scaled against, which is the
    side that can actually overspend."""
    from dashboard.backend.domain.leaderboard.real_trading import get_allocation

    account_value = account_cash + sum(holdings.get(sym, 0) * prices.get(sym, 0) for sym in holdings)
    return min(get_allocation(strategy_key), account_value)


def effective_target_weights(
    strategy_key: str, target_weights: Dict[str, float], portfolio_value: float,
) -> Dict[str, float]:
    """If this strategy has a fixed per-stock dollar amount configured (the
    Strategy Catalog's "$ per stock" field,
    real_trading.py::get_per_stock_amount), convert its target weights into
    the effective weights that would produce that fixed dollar amount per
    symbol against `portfolio_value` -- every symbol it wants to hold gets
    the same dollar size, not a proportional split of the allocation. Falls
    back to the strategy's own weights unchanged if no override is set."""
    from dashboard.backend.domain.leaderboard.real_trading import get_per_stock_amount

    per_stock = get_per_stock_amount(strategy_key)
    if not per_stock or portfolio_value <= 0:
        return target_weights
    return {sym: per_stock / portfolio_value for sym, w in target_weights.items() if w and w > 0}


def compute_rebalance_orders(
    target_weights: Dict[str, float],
    portfolio_value: float,
    holdings: Dict[str, float],
    prices: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Diff a strategy's target weights against actual current holdings to
    produce the buy/sell orders needed to close the gap. Pure function -- no
    broker calls. A symbol held but absent from `target_weights` is fully
    liquidated (target weight 0)."""
    orders: List[Dict[str, Any]] = []
    universe = set(target_weights) | set(holdings)
    for symbol in sorted(universe):
        price = prices.get(symbol)
        if not price or price <= 0:
            continue  # no_quote is caught by risk_gate_orders downstream
        target_qty = (target_weights.get(symbol, 0.0) * portfolio_value) / price
        current_qty = float(holdings.get(symbol, 0) or 0)
        delta = target_qty - current_qty
        if abs(delta * price) < 1.0:  # sub-$1 rebalance isn't worth a round trip
            continue
        side = "buy" if delta > 0 else "sell"
        orders.append({"symbol": symbol, "side": side, "quantity": abs(delta)})
    return orders
