"""Stochastic oscillator: %K crossing above %D out of oversold buys; exit
overbought. The classic oscillator play. Promoted from the Three-Year
Strategy Gauntlet (+26.5%, max DD -25.6%, Sharpe 0.51 over
2023-08..2026-08)."""

from __future__ import annotations

import pandas as pd

from ._signal_engine import DailyHistory
from ._tv_classic import EntryExitClassic


def _k(history: DailyHistory, sym: str, n: int = 14) -> pd.Series:
    close = history.close[sym].dropna()
    high = history.high[sym].reindex(close.index)
    low = history.low[sym].reindex(close.index)
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    return (100 * (close - ll) / (hh - ll)).dropna()


class TvStochasticStrategy(EntryExitClassic):
    key = "tv_stochastic"
    _max_positions = 8
    _min_history = 20

    def _entry(self, history: DailyHistory, sym: str) -> bool:
        k = _k(history, sym)
        if len(k) < 5:
            return False
        d = k.rolling(3).mean()
        return k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] < 30

    def _exit(self, history: DailyHistory, sym: str) -> bool:
        k = _k(history, sym)
        return len(k) >= 1 and k.iloc[-1] > 80
