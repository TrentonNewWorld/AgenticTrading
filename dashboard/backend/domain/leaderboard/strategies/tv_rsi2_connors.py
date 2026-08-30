"""Larry Connors' RSI-2: buy deep two-day-RSI dips in SPY while it holds
above its 200-day average; sell the bounce. One of the most republished
mean-reversion systems on TradingView. Promoted from the Three-Year Strategy
Gauntlet (+19.1%, max DD -7.7%, Sharpe 0.94 over 2023-08..2026-08)."""

from __future__ import annotations

from ._indicators import rsi
from ._signal_engine import DailyHistory
from ._tv_classic import EntryExitClassic


class TvRsi2ConnorsStrategy(EntryExitClassic):
    key = "tv_rsi2_connors"
    _universe = ["SPY"]
    _max_positions = 1
    _min_history = 210

    def _entry(self, history: DailyHistory, sym: str) -> bool:
        close = history.close[sym].dropna()
        if len(close) < 210:
            return False
        sma200 = close.iloc[-200:].mean()
        r = rsi(close.to_frame("x"), 2)["x"].iloc[-1]
        return close.iloc[-1] > sma200 and r < 10

    def _exit(self, history: DailyHistory, sym: str) -> bool:
        close = history.close[sym].dropna()
        if len(close) < 3:
            return False
        return rsi(close.to_frame("x"), 2)["x"].iloc[-1] > 65
