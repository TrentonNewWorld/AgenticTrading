"""Day-by-day simulation of a sandboxed decide() against real daily bars.

Shared by ``backtester.run_backtest`` (Testing-page: fetches its own year of
bars, then simulates) and ``domain.leaderboard.strategies.sandboxed`` (an
already-added strategy plugged into the Strategy Catalog, which fetches bars
itself via ``catalog._compute_all`` and only needs the simulation step) --
one loop, so a strategy tested here behaves identically once it's live on
the Catalog.
"""

from __future__ import annotations

from datetime import date as date_cls
from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.domain.manual10.sandbox import run_decide
from dashboard.backend.execution._alpaca_strategy_shared import compute_rebalance_orders


def simulate(
    code: str,
    bars_by_symbol: Dict[str, "pd.DataFrame"],
    test_dates: List[date_cls],
    initial_capital: float,
    *,
    timeout_per_day: int = 10,
) -> List[Dict[str, Any]]:
    """Returns [{"date": "YYYY-MM-DD", "equity": float}, ...], one row per
    trading day in ``test_dates``. ``bars_by_symbol`` may include history
    before ``test_dates[0]`` -- that's given to decide() as pre-window
    context, never traded on directly (no look-ahead: only rows up to and
    including the current day are ever handed to the sandbox)."""
    cash = float(initial_capital)
    holdings: Dict[str, float] = {}
    curve: List[Dict[str, Any]] = []
    test_date_set = set(test_dates)

    for day in test_dates:
        price_history: Dict[str, List[Dict[str, Any]]] = {}
        prices_today: Dict[str, float] = {}
        for symbol, df in bars_by_symbol.items():
            history_rows = [
                {"t": str(idx.date()), "close": float(row["close"])}
                for idx, row in df.iterrows()
                if idx.date() <= day
            ]
            if not history_rows:
                continue
            price_history[symbol] = history_rows
            if history_rows[-1]["t"] == day.isoformat():
                prices_today[symbol] = history_rows[-1]["close"]

        if not prices_today:
            continue

        weights = run_decide(code, price_history, timeout=timeout_per_day)

        position_value = sum(holdings.get(s, 0) * prices_today.get(s, 0) for s in holdings)
        equity_before = cash + position_value

        if weights:
            orders = compute_rebalance_orders(weights, equity_before, holdings, prices_today)
            for order in orders:
                symbol = order["symbol"]
                qty = order["quantity"]
                price = prices_today.get(symbol)
                if not price:
                    continue
                if order["side"] == "buy":
                    cost = qty * price
                    if cost > cash + 0.01:
                        qty = cash / price
                        cost = qty * price
                    if qty <= 0:
                        continue
                    cash -= cost
                    holdings[symbol] = holdings.get(symbol, 0) + qty
                else:
                    qty = min(qty, holdings.get(symbol, 0))
                    if qty <= 0:
                        continue
                    cash += qty * price
                    holdings[symbol] = holdings.get(symbol, 0) - qty
                    if holdings[symbol] <= 1e-9:
                        del holdings[symbol]

        equity_after = cash + sum(holdings.get(s, 0) * prices_today.get(s, 0) for s in holdings)
        if day in test_date_set:
            curve.append({"date": day.isoformat(), "equity": equity_after})

    return curve
