"""EMA ribbon 8/21/55 on SPY: hold only while the ribbon is stacked bullish
(fast over medium over slow). A TradingView perennial. Promoted from the
Three-Year Strategy Gauntlet (+28.6%, max DD -8.2%, Sharpe 0.94 over
2023-08..2026-08)."""

from __future__ import annotations

from typing import Dict

from ._signal_engine import DailyHistory
from ._tv_classic import WeightFnClassic


class TvEmaRibbonStrategy(WeightFnClassic):
    key = "tv_ema_ribbon"
    _universe = ["SPY"]

    def _weights(self, history: DailyHistory) -> Dict[str, float]:
        close = history.close["SPY"].dropna()
        if len(close) < 60:
            return {}
        e8 = close.ewm(span=8, adjust=False).mean().iloc[-1]
        e21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
        e55 = close.ewm(span=55, adjust=False).mean().iloc[-1]
        return {"SPY": 1.0} if e8 > e21 > e55 else {}
