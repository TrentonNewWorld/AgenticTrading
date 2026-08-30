"""Simplified Ichimoku cloud trend: hold SPY while price sits above the
cloud with the conversion line over the base line. Best risk-adjusted TV
classic in the Three-Year Strategy Gauntlet (+55.2%, max DD only -6.8%,
Sharpe 1.64 over 2023-08..2026-08)."""

from __future__ import annotations

from typing import Dict

from ._signal_engine import DailyHistory
from ._tv_classic import WeightFnClassic


class TvIchimokuStrategy(WeightFnClassic):
    key = "tv_ichimoku"
    _universe = ["SPY"]

    def _weights(self, history: DailyHistory) -> Dict[str, float]:
        high = history.high["SPY"].dropna()
        low = history.low["SPY"].dropna()
        close = history.close["SPY"].dropna()
        if len(close) < 78:  # 52-period span computed 26 days back
            return {}
        conv = (high.iloc[-9:].max() + low.iloc[-9:].min()) / 2
        base = (high.iloc[-26:].max() + low.iloc[-26:].min()) / 2
        h26, l26 = high.iloc[:-26], low.iloc[:-26]
        span_a = (
            (h26.iloc[-9:].max() + l26.iloc[-9:].min()) / 2
            + (h26.iloc[-26:].max() + l26.iloc[-26:].min()) / 2
        ) / 2
        span_b = (h26.iloc[-52:].max() + l26.iloc[-52:].min()) / 2
        if close.iloc[-1] > max(span_a, span_b) and conv > base:
            return {"SPY": 1.0}
        return {}
