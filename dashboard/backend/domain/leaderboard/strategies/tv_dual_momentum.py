"""Gary Antonacci's dual momentum, absolute-momentum leg: hold SPY while its
trailing 12-month return is positive, otherwise cash. Promoted from the
Three-Year Strategy Gauntlet (+82.8%, max DD -18.8%, Sharpe 1.39 over
2023-08..2026-08 -- effectively matched SPY with an escape hatch)."""

from __future__ import annotations

from typing import Dict

from ._signal_engine import DailyHistory
from ._tv_classic import WeightFnClassic


class TvDualMomentumStrategy(WeightFnClassic):
    key = "tv_dual_momentum"
    _universe = ["SPY"]
    _rebalance_every_days = 21

    def _weights(self, history: DailyHistory) -> Dict[str, float]:
        close = history.close["SPY"].dropna()
        if len(close) < 253:
            return {}
        return {"SPY": 1.0} if close.iloc[-1] / close.iloc[-253] - 1 > 0 else {}
