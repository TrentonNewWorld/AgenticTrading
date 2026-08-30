"""Bollinger Band mean reversion: buy closes below the lower (20, 2) band,
exit at the midline -- a TradingView staple. Promoted from the Three-Year
Strategy Gauntlet (+41.4%, max DD -27.2%, Sharpe 0.64 over
2023-08..2026-08)."""

from __future__ import annotations

from ._indicators import bollinger
from ._signal_engine import DailyHistory
from ._tv_classic import EntryExitClassic


class TvBollingerMeanrevStrategy(EntryExitClassic):
    key = "tv_bollinger_meanrev"
    _max_positions = 8
    _min_history = 21

    def _entry(self, history: DailyHistory, sym: str) -> bool:
        close = history.close[sym].dropna()
        if len(close) < 21:
            return False
        upper, mid, lower, _pct_b = bollinger(close.to_frame("x"), 20, 2.0)
        return close.iloc[-1] < lower["x"].iloc[-1]

    def _exit(self, history: DailyHistory, sym: str) -> bool:
        close = history.close[sym].dropna()
        if len(close) < 21:
            return False
        upper, mid, lower, _pct_b = bollinger(close.to_frame("x"), 20, 2.0)
        return close.iloc[-1] >= mid["x"].iloc[-1]
