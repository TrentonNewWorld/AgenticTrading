"""Deterministic reading of the Marketplace "AI Hedge Fund" template
(``dashboard/config/marketplace.json``).

Approximates virattt's ai-hedge-fund analyst-panel concept as a composite
score: 12-month momentum skipping the most recent month (technical, 50%),
low realized volatility as a quality/valuation stand-in (30%), and 5-day
return as a sentiment stand-in (20%). Holds the top 8 scorers, rebalanced
monthly. No fundamentals/news data feed is available here, so those parts of
the real analyst panel aren't represented. Validated in the "Strategy Lab"
backtest report.

Lookback caveat: wants 252 days of history for the momentum term, capped to
whatever is actually available (see ``available_window``).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._indicators import zscore_row
from ._signal_engine import DailyHistory, available_window, decide_live, run_daily_signal_strategy

_TOP_N = 8
_REBALANCE_DAYS = 21
_MIN_HISTORY = 26


class AIHedgeFundStrategy(BaselineStrategy):
    key = "ai_hedge_fund"

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _weight_fn(self, history: DailyHistory, cur_date, day_index) -> Dict[str, float]:
        n = len(history)
        if n < _MIN_HISTORY:
            return {}
        close = history.close
        ret1 = close.pct_change()
        w252 = min(252, n - 1)
        w21 = min(21, n - 1)
        w60 = available_window(history, 60)
        # 12-month momentum, skipping the most recent month.
        tech = (close.iloc[-1 - w21] / close.iloc[-1 - w252] - 1) if w252 > w21 else pd.Series(0.0, index=close.columns)
        quality = -ret1.iloc[-w60:].std()
        w5 = min(5, n - 1)
        sentiment = ret1.iloc[-w5:].mean()
        composite = 0.5 * zscore_row(tech) + 0.3 * zscore_row(quality) + 0.2 * zscore_row(sentiment)
        ranked = composite.dropna().sort_values(ascending=False)
        picks = ranked.head(_TOP_N)
        if picks.empty:
            return {}
        shifted = picks - picks.min() + 0.1
        return (shifted / shifted.sum()).to_dict()

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
