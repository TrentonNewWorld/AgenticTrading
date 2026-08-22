"""Deterministic reading of the Marketplace "Volatility Guard" template
(``dashboard/config/marketplace.json``).

Holds the top 8 momentum stocks at full weight while 10-day price volatility
stays under 1.2x its 60-day average; cuts to the top 4 names above that, and
the top 2 above 1.6x -- reducing exposure as volatility spikes rather than
trimming every position by a fraction. Validated in the "Strategy Lab"
backtest report.

Lookback caveat: wants 60 days of history for the volatility-ratio term;
below that, holds nothing.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._signal_engine import DailyHistory, available_window, decide_live, run_daily_signal_strategy

_REBALANCE_DAYS = 5
_MIN_HISTORY = 61
_MAX_PICKS = 8


class VolatilityGuardStrategy(BaselineStrategy):
    key = "volatility_guard"

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _weight_fn(self, history: DailyHistory, cur_date, day_index) -> Dict[str, float]:
        n = len(history)
        if n < _MIN_HISTORY:
            return {}
        close = history.close
        ret1 = close.pct_change()
        w20_offset = min(20, n - 1)
        w10 = available_window(history, 10)
        w60 = available_window(history, 60)
        mom20 = close.iloc[-1] / close.iloc[-1 - w20_offset] - 1
        ranked = mom20.dropna().sort_values(ascending=False)
        picks = ranked.head(_MAX_PICKS).index.tolist()
        if not picks:
            return {}
        vol10 = ret1[picks].iloc[-w10:].std().mean()
        vol60 = ret1[picks].iloc[-w60:].std().mean()
        ratio = vol10 / vol60 if vol60 and not pd.isna(vol60) else 1.0
        if ratio < 1.2:
            exposure, n_picks = 1.0, 8
        elif ratio < 1.6:
            exposure, n_picks = 0.5, 4
        else:
            exposure, n_picks = 0.25, 2
        active = picks[:n_picks]
        if not active:
            return {}
        base_w = exposure / len(active)
        return {s: base_w for s in active}

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
