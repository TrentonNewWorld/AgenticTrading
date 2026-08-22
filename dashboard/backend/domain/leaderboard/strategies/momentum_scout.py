"""Deterministic reading of the Marketplace "Momentum Scout" template
(``dashboard/config/marketplace.json``).

Hand-translated from its prompt: ranks by 10-day price momentum confirmed by
above-average trading volume, holds the top 6 names with positive momentum
(moves to cash otherwise), and adds extra weight to a name pulling back 1-6%
from its recent 20-day high within an intact uptrend. Validated in the
"Strategy Lab" backtest report.

Lookback caveat: wants 30 days of history (20-day rolling high + 30-day
average volume); below that, holds nothing.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._signal_engine import DailyHistory, available_window, decide_live, run_daily_signal_strategy

_TOP_N = 6
_REBALANCE_DAYS = 3
_MIN_HISTORY = 31


class MomentumScoutStrategy(BaselineStrategy):
    key = "momentum_scout"

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _weight_fn(self, history: DailyHistory, cur_date, day_index) -> Dict[str, float]:
        n = len(history)
        if n < _MIN_HISTORY:
            return {}
        close = history.close
        volume = history.volume
        w10_offset = min(10, n - 1)
        w30 = available_window(history, 30)
        w20 = available_window(history, 20)
        mom10 = close.iloc[-1] / close.iloc[-1 - w10_offset] - 1
        vol_avg30 = volume.rolling(w30).mean().iloc[-1]
        vol_ratio = (volume.iloc[-1] / vol_avg30).clip(upper=3)
        score = mom10 * (1 + 0.3 * (vol_ratio - 1))
        ranked = score.dropna().sort_values(ascending=False)
        picks = [s for s in ranked.index if ranked[s] > 0][:_TOP_N]
        if not picks:
            return {}
        base_w = 1.0 / len(picks)
        roll_high20 = close.rolling(w20).max().iloc[-1]
        weights: Dict[str, float] = {}
        for sym in picks:
            px = close.iloc[-1][sym]
            hi = roll_high20.get(sym)
            pullback = (px - hi) / hi if hi else 0.0
            wt = base_w
            if -0.06 <= pullback < -0.01:
                wt *= 1.3
            weights[sym] = wt
        total = sum(weights.values())
        if total > 1.0:
            weights = {k: v / total for k, v in weights.items()}
        return weights

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
