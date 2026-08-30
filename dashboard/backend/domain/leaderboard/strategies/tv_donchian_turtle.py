"""Turtle-style Donchian channel breakout: buy a 20-day-high breakout, exit
on a 10-day low -- the system the original Turtle Traders were taught.
Promoted from the Three-Year Strategy Gauntlet (+51.1%, max DD -14.2%,
Sharpe 1.00 over 2023-08..2026-08)."""

from __future__ import annotations

from ._signal_engine import DailyHistory
from ._tv_classic import EntryExitClassic


class TvDonchianTurtleStrategy(EntryExitClassic):
    key = "tv_donchian_turtle"
    _max_positions = 8
    _min_history = 21

    def _entry(self, history: DailyHistory, sym: str) -> bool:
        close = history.close[sym].dropna()
        high = history.high[sym].dropna()
        if len(close) < 21:
            return False
        return close.iloc[-1] >= high.iloc[-21:-1].max()

    def _exit(self, history: DailyHistory, sym: str) -> bool:
        close = history.close[sym].dropna()
        low = history.low[sym].dropna()
        if len(close) < 11:
            return False
        return close.iloc[-1] <= low.iloc[-11:-1].min()
