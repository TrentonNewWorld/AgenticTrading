"""AlmgrenChriss / TWAP (merged), from the freqtrade-strategies community
repository (github.com/freqtrade/freqtrade-strategies, user_data/strategies
/{AlmgrenChrissStrategy,TWAPStrategy}.py).

Buys any stock whose RSI drops below 45 and exits once RSI recovers above
50. Both source strategies use this identical directional RSI signal,
differing only in how they'd slice an intraday order (an Almgren-Chriss
optimal-execution model vs. plain time-weighted averaging) -- a distinction
that disappears entirely once decisions are made once per day rather than
sliced across the trading session, so one strategy stands in for both. The
original also shorts above RSI 55; that leg is dropped for long-only
consistency with the rest of this dashboard's backtest engine.

Lookback: needs ~15 days for RSI to stabilize; below that, holds whatever is
already held and takes no new positions.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._indicators import rsi
from ._signal_engine import (
    DailyHistory,
    decide_live,
    load_strategy_state,
    make_entry_exit_weight_fn,
    run_daily_signal_strategy,
    save_strategy_state,
)

_MAX_POSITIONS = 8
_MIN_HISTORY = 15


class AlmgrenChrissTwapStrategy(BaselineStrategy):
    key = "almgren_chriss_twap"

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _entry(self, history: DailyHistory, sym: str) -> bool:
        close = history.close[sym].dropna().to_frame()
        if len(close) < _MIN_HISTORY:
            return False
        return bool(rsi(close, 14).iloc[-1, 0] < 45)

    def _exit(self, history: DailyHistory, sym: str) -> bool:
        close = history.close[sym].dropna().to_frame()
        if len(close) < _MIN_HISTORY + 1:
            return False
        r = rsi(close, 14)
        return bool(r.iloc[-1, 0] > 50 and r.iloc[-2, 0] <= 50)

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

        weight_fn = make_entry_exit_weight_fn(self._entry, self._exit, symbols, _MAX_POSITIONS, _MIN_HISTORY)
        curve, n_trades = run_daily_signal_strategy(
            bars_subset, start_date, end_date, initial_capital, weight_fn,
            rebalance_every_days=1,
        )
        self._num_trades = n_trades
        return curve

    def num_trades(self) -> int:
        return getattr(self, "_num_trades", 0)

    def decide(self, history: DailyHistory) -> Dict[str, float]:
        """Live/paper-trading entrypoint (see bandtastic.py for the pattern)."""
        symbols = self.required_symbols()
        state = load_strategy_state(self.key)
        weight_fn = make_entry_exit_weight_fn(
            self._entry, self._exit, symbols, _MAX_POSITIONS, _MIN_HISTORY,
            initial_held=state.get("held"),
        )
        weights = decide_live(weight_fn, history)
        save_strategy_state(self.key, {"held": sorted(weight_fn.held)})
        return weights
