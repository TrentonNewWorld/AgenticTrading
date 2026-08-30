"""Wilder's directional system: only trade when ADX(14) confirms a trend
(>25) with +DI above -DI; exit when the DIs cross back. Promoted from the
Three-Year Strategy Gauntlet (+41.3%, max DD -31.6%, Sharpe 0.69 over
2023-08..2026-08)."""

from __future__ import annotations

from ._indicators import adx
from ._signal_engine import DailyHistory
from ._tv_classic import EntryExitClassic


def _dmi(history: DailyHistory, sym: str):
    close = history.close[sym].dropna().to_frame("x")
    high = history.high[sym].reindex(close.index).to_frame("x")
    low = history.low[sym].reindex(close.index).to_frame("x")
    a, plus, minus = adx(high, low, close, 14)
    return a["x"].iloc[-1], plus["x"].iloc[-1], minus["x"].iloc[-1]


class TvAdxDmiStrategy(EntryExitClassic):
    key = "tv_adx_dmi"
    _max_positions = 8
    _min_history = 30

    def _entry(self, history: DailyHistory, sym: str) -> bool:
        if len(history.close[sym].dropna()) < 30:
            return False
        a, p, m = _dmi(history, sym)
        return bool(a > 25 and p > m)

    def _exit(self, history: DailyHistory, sym: str) -> bool:
        if len(history.close[sym].dropna()) < 30:
            return False
        a, p, m = _dmi(history, sym)
        return bool(p < m)
