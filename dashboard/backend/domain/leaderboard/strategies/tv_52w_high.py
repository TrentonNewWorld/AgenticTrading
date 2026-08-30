"""52-week-high momentum: hold the eight Dow names trading closest to their
own yearly high, refreshed monthly -- an academic anomaly the community
rediscovers constantly. Promoted from the Three-Year Strategy Gauntlet
(+55.6%, max DD -15.4%, Sharpe 1.13 over 2023-08..2026-08)."""

from __future__ import annotations

from typing import Dict

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from ._signal_engine import DailyHistory
from ._tv_classic import WeightFnClassic


class Tv52wHighStrategy(WeightFnClassic):
    key = "tv_52w_high"
    _universe = list(DJIA_30)
    _rebalance_every_days = 21

    def _weights(self, history: DailyHistory) -> Dict[str, float]:
        if len(history) < 60:
            return {}
        scores = {}
        for sym in self.required_symbols():
            close = history.close[sym].dropna()
            if len(close) < 60:
                continue
            window = close.iloc[-252:] if len(close) >= 252 else close
            scores[sym] = close.iloc[-1] / window.max()
        top = sorted(scores, key=scores.get, reverse=True)[:8]
        return {s: 1.0 / len(top) for s in top} if top else {}
