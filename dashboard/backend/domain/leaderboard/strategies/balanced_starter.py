"""Deterministic reading of the Marketplace "Balanced Starter" template
(``dashboard/config/marketplace.json``).

The template itself is a natural-language prompt for an LLM agent, not a
numeric formula -- this is a hand-translated, explicit rule capturing its
described behavior: equal-weight the top 8 Dow stocks by 20-day momentum,
overweight a name trading 3%+ below its 20-day average (a dip-buy), and trim
a name that has run up 15%+ over the past month (a profit-take), capped at
20% per position. Validated in the "Strategy Lab" backtest report.

Lookback caveat: wants 20 days of history for both the ranking and the
dip/run-up checks; below that, holds nothing.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._signal_engine import DailyHistory, available_window, decide_live, run_daily_signal_strategy

_TOP_N = 8
_CAP = 0.20
_REBALANCE_DAYS = 5
_MIN_HISTORY = 21


class BalancedStarterStrategy(BaselineStrategy):
    key = "balanced_starter"

    PARAM_SCHEMA = {
        "top_n": {"label": "Positions held", "type": "int", "default": _TOP_N, "min": 1, "max": 30},
        "rebalance_days": {"label": "Rebalance every (trading days)", "type": "int", "default": _REBALANCE_DAYS, "min": 1, "max": 63},
        "cap": {"label": "Max weight per position", "type": "float", "default": _CAP, "min": 0.05, "max": 1.0},
    }

    def __init__(self, config):
        super().__init__(config)
        self._top_n = self.config.get("top_n", _TOP_N)
        self._rebalance_days = self.config.get("rebalance_days", _REBALANCE_DAYS)
        self._cap = self.config.get("cap", _CAP)

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _weight_fn(self, history: DailyHistory, cur_date, day_index) -> Dict[str, float]:
        n = len(history)
        if n < _MIN_HISTORY:
            return {}
        close = history.close
        offset = min(20, n - 1)
        window = available_window(history, 20)
        mom20 = close.iloc[-1] / close.iloc[-1 - offset] - 1
        sma20 = close.rolling(window).mean().iloc[-1]
        ranked = mom20.dropna().sort_values(ascending=False)
        top = ranked.head(self._top_n).index.tolist()
        if not top:
            return {}
        base_w = 1.0 / len(top)
        weights: Dict[str, float] = {}
        for sym in top:
            px = close.iloc[-1][sym]
            sma = sma20.get(sym)
            dev = (px - sma) / sma if sma and not pd.isna(sma) else 0.0
            wt = base_w
            if dev < -0.03:
                wt *= 1.4
            run_up = mom20.get(sym)
            if run_up and run_up > 0.15:
                wt *= 0.6
            weights[sym] = min(wt, self._cap)
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
            rebalance_every_days=self._rebalance_days,
        )
        self._num_trades = n_trades
        return curve

    def num_trades(self) -> int:
        return getattr(self, "_num_trades", 0)

    def decide(self, history: DailyHistory) -> Dict[str, float]:
        return decide_live(self._weight_fn, history)
