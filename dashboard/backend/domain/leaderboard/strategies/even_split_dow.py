"""Deterministic reading of the Marketplace "Even-Split Dow" template
(``dashboard/config/marketplace.json``).

Equal-weights all 30 Dow stocks, rebalanced monthly back to even shares.
Distinct from the leaderboard's own ``equal_weight_buyhold`` (never
rebalances) and ``equal_weight_index`` (rebalances every day) baselines.
Validated in the "Strategy Lab" backtest report.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._signal_engine import DailyHistory, decide_live, run_daily_signal_strategy

_REBALANCE_DAYS = 21


class EvenSplitDowStrategy(BaselineStrategy):
    key = "even_split_dow"

    PARAM_SCHEMA = {
        "rebalance_days": {"label": "Rebalance every (trading days)", "type": "int", "default": _REBALANCE_DAYS, "min": 1, "max": 63},
    }

    def __init__(self, config):
        super().__init__(config)
        self._rebalance_days = self.config.get("rebalance_days", _REBALANCE_DAYS)

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _weight_fn(self, history: DailyHistory, cur_date, day_index) -> Dict[str, float]:
        if history.close.empty:
            return {}
        last = history.close.iloc[-1]
        valid = [s for s in last.index if not pd.isna(last[s])]
        if not valid:
            return {}
        return {s: 1.0 / len(valid) for s in valid}

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
