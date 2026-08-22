"""Deterministic reading of the Marketplace "Sector Rotator" template
(``dashboard/config/marketplace.json``).

Groups the Dow 30 into 8 rough sectors, holds the top 3 names from whichever
sector had the best trailing-month average return, and rotates to a new
sector when the leader changes, checked monthly. Validated in the "Strategy
Lab" backtest report.

Lookback caveat: wants 20 days of history for the sector-momentum ranking;
below that, holds nothing. The sector groupings are fixed (not derived from
any live classification feed).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._signal_engine import DailyHistory, decide_live, run_daily_signal_strategy

_REBALANCE_DAYS = 21
_MIN_HISTORY = 21
_TOP_N = 3

_SECTORS = {
    "Technology": ["AAPL", "MSFT", "CRM", "IBM", "CSCO", "NVDA", "GOOGL"],
    "Financials": ["JPM", "GS", "AXP", "V"],
    "Healthcare": ["JNJ", "MRK", "AMGN", "UNH"],
    "Consumer": ["AMZN", "HD", "MCD", "NKE", "WMT", "KO", "DIS"],
    "Industrials": ["BA", "CAT", "HON", "MMM"],
    "Energy": ["CVX"],
    "Materials": ["SHW"],
    "Insurance": ["TRV"],
}


class SectorRotatorStrategy(BaselineStrategy):
    key = "sector_rotator"

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _weight_fn(self, history: DailyHistory, cur_date, day_index) -> Dict[str, float]:
        n = len(history)
        if n < _MIN_HISTORY:
            return {}
        close = history.close
        w20_offset = min(20, n - 1)
        mom20 = close.iloc[-1] / close.iloc[-1 - w20_offset] - 1
        sector_ret: Dict[str, float] = {}
        for sec, syms in _SECTORS.items():
            present = [s for s in syms if s in mom20.index]
            r = mom20[present].mean() if present else float("nan")
            if pd.notna(r):
                sector_ret[sec] = r
        if not sector_ret:
            return {}
        best_sec = max(sector_ret, key=sector_ret.get)
        candidates = [s for s in _SECTORS[best_sec] if s in mom20.index]
        ranked = mom20[candidates].dropna().sort_values(ascending=False)
        top = ranked.head(_TOP_N).index.tolist()
        if not top:
            return {}
        return {s: 1.0 / len(top) for s in top}

    def run(
        self,
        bars_by_symbol: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
        initial_capital: float,
    ) -> List[Dict[str, Any]]:
        symbols = self.required_symbols()
        bars_subset = {s: bars_by_symbol[s] for s in symbols if s in bars_by_symbol}
        if not bars_subset:
            return []

        curve, n_trades = run_daily_signal_strategy(
            bars_subset, start_date, end_date, initial_capital, self._weight_fn,
            rebalance_every_days=_REBALANCE_DAYS,
        )
        self._num_trades = n_trades
        return curve

    def num_trades(self) -> int:
        return getattr(self, "_num_trades", 0)

    def decide(self, history: DailyHistory) -> Dict[str, float]:
        return decide_live(self._weight_fn, history)
