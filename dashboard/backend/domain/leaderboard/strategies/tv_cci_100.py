"""Donald Lambert's CCI ridden as a momentum trigger: enter above +100,
exit when CCI turns negative. Promoted from the Three-Year Strategy
Gauntlet (+25.5%, max DD -22.5%, Sharpe 0.57 over 2023-08..2026-08)."""

from __future__ import annotations

from ._signal_engine import DailyHistory
from ._tv_classic import EntryExitClassic


def _cci(history: DailyHistory, sym: str, n: int = 20) -> float:
    close = history.close[sym].dropna()
    high = history.high[sym].reindex(close.index)
    low = history.low[sym].reindex(close.index)
    if len(close) < n:
        return 0.0
    tp = ((high + low + close) / 3).iloc[-n:]
    mean = tp.mean()
    mad = (tp - mean).abs().mean()
    if mad == 0:
        return 0.0
    return float((tp.iloc[-1] - mean) / (0.015 * mad))


class TvCci100Strategy(EntryExitClassic):
    key = "tv_cci_100"
    _max_positions = 8
    _min_history = 21

    def _entry(self, history: DailyHistory, sym: str) -> bool:
        return _cci(history, sym) > 100

    def _exit(self, history: DailyHistory, sym: str) -> bool:
        return _cci(history, sym) < 0
