"""Direct market-index tracker (e.g. DJIA via ^DJI, S&P 500 via ^GSPC).

Displays the index level itself: equity = initial_capital * level / level_0.

For index symbols (starting with "^") the real index series is fetched from
Yahoo Finance, since Alpaca only serves tradeable ETFs (DIA/SPY) which drift
off the index. For a plain ticker, the Alpaca bars are normalized instead.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BaselineStrategy
from ._common import build_price_cache, market_timestamps
from ._signal_engine import DailyHistory, load_strategy_state, save_strategy_state
from ._yahoo import fetch_index_hourly


class MarketIndexStrategy(BaselineStrategy):
    key = "market_index"

    def _configured_symbol(self) -> str:
        symbols = self.config.get("symbols") or []
        return symbols[0] if symbols else ""

    def required_symbols(self) -> List[str]:
        # Index symbols are fetched from Yahoo, not Alpaca, so they are excluded
        # from the shared Alpaca fetch.
        return [s for s in (self.config.get("symbols") or []) if not s.startswith("^")]

    def _curve_from_levels(
        self,
        levels: List[Any],
        initial_capital: float,
    ) -> List[Dict[str, Any]]:
        base = levels[0][1]
        if not base:
            return []
        curve: List[Dict[str, Any]] = []
        for ts, level in levels:
            equity = initial_capital * (level / base)
            curve.append(
                {
                    "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "equity": round(equity, 2),
                    "cash": 0,
                    "positions_value": round(equity, 2),
                    "daily_return": 0,
                }
            )
        return curve

    def run(
        self,
        bars_by_symbol: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
        initial_capital: float,
    ) -> List[Dict[str, Any]]:
        symbol = self._configured_symbol()
        if not symbol:
            return []

        # Real index series from Yahoo (^DJI, ^GSPC, ...).
        if symbol.startswith("^"):
            levels = fetch_index_hourly(symbol, start_date, end_date)
            if not levels:
                return []
            return self._curve_from_levels(levels, initial_capital)

        # Fallback: normalize a tradeable ticker's Alpaca bars.
        df = bars_by_symbol.get(symbol)
        if df is None or df.empty:
            return []
        timestamps = market_timestamps({symbol: df})
        if not timestamps:
            return []
        prices = build_price_cache({symbol: df}, timestamps).get(symbol)
        if not prices:
            return []
        levels = [(ts, prices[ts]) for ts in timestamps]
        return self._curve_from_levels(levels, initial_capital)

    def decide(self, history: DailyHistory) -> Optional[Dict[str, float]]:
        """Live/paper-trading entrypoint. A ``^``-prefixed symbol (e.g. ^DJI,
        ^GSPC) is a synthetic index level, not a tradeable security -- there
        is nothing to buy, so this returns ``None`` for those configs rather
        than inventing an ETF substitute the config didn't ask for. For a
        real tradeable ticker (e.g. DIA, SPY), behaves like buy & hold: a
        one-time 100% allocation, then ``None`` (no further rebalancing) on
        every subsequent call."""
        symbol = self._configured_symbol()
        if not symbol or symbol.startswith("^"):
            return None
        state = load_strategy_state(self.key)
        if state.get("established"):
            return None
        if symbol not in history.close.columns or pd.isna(history.close[symbol].iloc[-1]):
            return None
        save_strategy_state(self.key, {"established": True})
        return {symbol: 1.0}
