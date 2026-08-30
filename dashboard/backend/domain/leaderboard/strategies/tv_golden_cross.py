"""The Golden Cross: hold SPY while its 50-day average is above its 200-day
average, sit in cash after the death cross. The most famous trend signal in
retail trading. Promoted from the Three-Year Strategy Gauntlet (+55.2%,
max DD -18.8%, Sharpe 1.07 over 2023-08..2026-08)."""

from __future__ import annotations

from typing import Dict

from ._signal_engine import DailyHistory
from ._tv_classic import WeightFnClassic


class TvGoldenCrossStrategy(WeightFnClassic):
    key = "tv_golden_cross"
    _universe = ["SPY"]

    def _weights(self, history: DailyHistory) -> Dict[str, float]:
        close = history.close["SPY"].dropna()
        if len(close) < 200:
            return {}
        if close.iloc[-50:].mean() > close.iloc[-200:].mean():
            return {"SPY": 1.0}
        return {}
