"""Deterministic reading of the Marketplace "Contrarian Dip Buyer" template
(``dashboard/config/marketplace.json``).

Buys a stock in increasing tranches (up to 3) the further it falls below its
20-day high (10%/15%/20% thresholds), with portfolio weight proportional to
how deep the dip is; exits the whole position once the stock recovers to
within 2% of that high. Validated in the "Strategy Lab" backtest report --
the single best-performing strategy of the 30 tested there.

State: each symbol's tranche level (0-3) must survive both a full ``run()``
backtest (checked every day) and separate ``decide()`` invocations in
live/paper trading, persisted the same way ``pattern_recognition.py``
persists its entry-day map.

Lookback caveat: wants 20 days of history for the rolling high; below that,
holds nothing.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._signal_engine import (
    DailyHistory,
    available_window,
    decide_live,
    load_strategy_state,
    run_daily_signal_strategy,
    save_strategy_state,
)

_MIN_HISTORY = 21


class ContrarianDipBuyerStrategy(BaselineStrategy):
    key = "contrarian_dip_buyer"

    def __init__(self, config):
        super().__init__(config)
        self._tranche_state: Dict[str, int] = {}

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _weight_fn(self, history: DailyHistory, cur_date, day_index) -> Dict[str, float]:
        n = len(history)
        if n < _MIN_HISTORY:
            return {}
        close = history.close.iloc[-1]
        window = available_window(history, 20)
        roll_high = history.close.rolling(window).max().iloc[-1]
        active: List[str] = []
        for sym in history.close.columns:
            px, hi = close.get(sym), roll_high.get(sym)
            if px is None or hi is None or pd.isna(px) or pd.isna(hi) or not hi:
                continue
            dd = (px - hi) / hi
            state = self._tranche_state.get(sym, 0)
            if dd <= -0.10:
                state = min(3, state + 1) if dd <= -0.20 else max(state, 1)
                if dd <= -0.15:
                    state = max(state, 2)
            if dd >= -0.02:
                state = 0
            self._tranche_state[sym] = state
            if state > 0:
                active.append(sym)
        if not active:
            return {}
        total_tranches = sum(self._tranche_state[s] for s in active)
        return {s: self._tranche_state[s] / total_tranches for s in active}

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

        self._tranche_state = {}
        curve, n_trades = run_daily_signal_strategy(
            bars_subset, start_date, end_date, initial_capital, self._weight_fn,
            rebalance_every_days=1,
        )
        self._num_trades = n_trades
        return curve

    def num_trades(self) -> int:
        return getattr(self, "_num_trades", 0)

    def decide(self, history: DailyHistory) -> Dict[str, float]:
        state = load_strategy_state(self.key)
        self._tranche_state = dict(state.get("tranche_state") or {})
        weights = decide_live(self._weight_fn, history)
        save_strategy_state(self.key, {"tranche_state": self._tranche_state})
        return weights
