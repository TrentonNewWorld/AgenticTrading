"""Larry Williams' %R(14): buy washed-out readings below -80, exit on
recovery above -20. Promoted from the Three-Year Strategy Gauntlet (+51.7%,
max DD -18.5%, Sharpe 0.93 over 2023-08..2026-08)."""

from __future__ import annotations

from ._signal_engine import DailyHistory
from ._tv_classic import EntryExitClassic


def _wr(history: DailyHistory, sym: str, n: int = 14) -> float:
    close = history.close[sym].dropna()
    high = history.high[sym].reindex(close.index)
    low = history.low[sym].reindex(close.index)
    if len(close) < n:
        return 0.0
    hh = high.iloc[-n:].max()
    ll = low.iloc[-n:].min()
    if hh == ll:
        return 0.0
    return float(-100 * (hh - close.iloc[-1]) / (hh - ll))


class TvWilliamsRStrategy(EntryExitClassic):
    key = "tv_williams_r"
    _max_positions = 8
    _min_history = 15

    def _entry(self, history: DailyHistory, sym: str) -> bool:
        return _wr(history, sym) < -80

    def _exit(self, history: DailyHistory, sym: str) -> bool:
        return _wr(history, sym) > -20
