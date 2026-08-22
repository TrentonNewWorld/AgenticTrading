"""Deterministic reading of the Marketplace "Three-Step Analyst" template
(``dashboard/config/marketplace.json``).

A simplified stand-in for the template's 3-stage facts-to-signal-to-orders
pipeline: only holds stocks where the 20-day average price is above the
50-day average (an uptrend filter), sized by how strong that 20-day
momentum is, across up to 10 names. Validated in the "Strategy Lab"
backtest report.

Lookback caveat: wants 50 days of history for the trend filter; below that,
holds nothing.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._signal_engine import DailyHistory, available_window, decide_live, run_daily_signal_strategy

_TOP_N = 10
_REBALANCE_DAYS = 5
_MIN_HISTORY = 51


class ThreeStepAnalystStrategy(BaselineStrategy):
    key = "three_step_analyst"

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _weight_fn(self, history: DailyHistory, cur_date, day_index) -> Dict[str, float]:
        n = len(history)
        if n < _MIN_HISTORY:
            return {}
        close = history.close
        w20 = available_window(history, 20)
        w50 = available_window(history, 50)
        w20_offset = min(20, n - 1)
        sma20 = close.rolling(w20).mean().iloc[-1]
        sma50 = close.rolling(w50).mean().iloc[-1]
        mom20 = close.iloc[-1] / close.iloc[-1 - w20_offset] - 1
        trend_up = sma20 > sma50
        strength = mom20.clip(lower=0)
        signal = strength.where(trend_up, 0)
        ranked = signal.dropna().sort_values(ascending=False)
        picks = ranked[ranked > 0].head(_TOP_N)
        if picks.empty:
            return {}
        return (picks / picks.sum()).to_dict()

    def run(
        self,
        bars_by_symbol: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
        initial_capital: float,
    ) -> List[Dict[str, Any]]:
        symbols = self.required_symbols()
        bars_subset = {s: bars_by_symbol[s] for s in symbols if s in bars_by_symbol}
        if not bars_subset:
            return []

        curve, n_trades = run_daily_signal_strategy(
            bars_subset, start_date, end_date, initial_capital, self._weight_fn,
            rebalance_every_days=_REBALANCE_DAYS,
        )
        self._num_trades = n_trades
        return curve

    def num_trades(self) -> int:
        return getattr(self, "_num_trades", 0)

    def decide(self, history: DailyHistory) -> Dict[str, float]:
        return decide_live(self._weight_fn, history)
