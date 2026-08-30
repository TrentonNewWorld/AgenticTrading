"""Shared bases for the TradingView-classic strategies added from the
Three-Year Strategy Gauntlet (2026-08-29). Every classic that cleared the
operator's 10%-return bar in that 3-year test was promoted from the gauntlet
script (dashboard/scripts/community_strategy_lab_3y.py) into a real registry
strategy so it shows on the Strategy page and can be activated for paper or
live trading.

Two shapes cover all twelve:

* ``EntryExitClassic`` -- per-symbol entry/exit rules over a held set (the
  supertrend_triple/bandtastic pattern, including live held-state
  persistence so an activated strategy's positions survive process
  restarts).
* ``WeightFnClassic`` -- a stateless target-weight function of history (the
  golden-cross shape); ``decide`` just evaluates today's weights, nothing to
  persist.

Kept in one module (rather than duplicating ~40 lines into each of twelve
files) because the twelve differ ONLY in their signal rules; the run/decide
plumbing is identical and a bug in it should be fixed in exactly one place.
Subclasses stay one-per-module per repo convention.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._signal_engine import (
    DailyHistory,
    decide_live,
    load_strategy_state,
    make_entry_exit_weight_fn,
    run_daily_signal_strategy,
    save_strategy_state,
)


class EntryExitClassic(BaselineStrategy):
    """Base for held-set classics. Subclasses set ``key``, ``_min_history``,
    ``_max_positions``, optionally ``_universe``, and implement
    ``_entry(history, sym)`` / ``_exit(history, sym)``."""

    _min_history: int = 2
    _max_positions: int = 8
    _universe: List[str] = list(DJIA_30)

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(self._universe)

    def _entry(self, history: DailyHistory, sym: str) -> bool:  # pragma: no cover - abstract-ish
        raise NotImplementedError

    def _exit(self, history: DailyHistory, sym: str) -> bool:  # pragma: no cover - abstract-ish
        raise NotImplementedError

    def run(
        self,
        bars_by_symbol: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
        initial_capital: float,
    ) -> List[Dict[str, Any]]:
        symbols = self.required_symbols()
        subset = {s: bars_by_symbol[s] for s in symbols if s in bars_by_symbol}
        if not subset:
            return []
        weight_fn = make_entry_exit_weight_fn(
            self._entry, self._exit, symbols, self._max_positions, self._min_history
        )
        curve, n_trades = run_daily_signal_strategy(
            subset, start_date, end_date, initial_capital, weight_fn,
            rebalance_every_days=1,
        )
        self._num_trades = n_trades
        return curve

    def num_trades(self) -> int:
        return getattr(self, "_num_trades", 0)

    def decide(self, history: DailyHistory) -> Dict[str, float]:
        symbols = self.required_symbols()
        state = load_strategy_state(self.key)
        weight_fn = make_entry_exit_weight_fn(
            self._entry, self._exit, symbols, self._max_positions, self._min_history,
            initial_held=state.get("held"),
        )
        weights = decide_live(weight_fn, history)
        save_strategy_state(self.key, {"held": sorted(weight_fn.held)})
        return weights


class WeightFnClassic(BaselineStrategy):
    """Base for stateless target-weight classics. Subclasses set ``key``,
    optionally ``_universe`` / ``_rebalance_every_days``, and implement
    ``_weights(history)`` returning {symbol: weight} for today.

    A monthly ``_rebalance_every_days`` (e.g. 21) only paces the *backtest*;
    ``decide`` evaluates the rule fresh each scheduler run. For the two
    monthly classics that is faithful in practice -- their rankings/signals
    move slowly, so daily evaluation converges to the same holdings.
    """

    _universe: List[str] = ["SPY"]
    _rebalance_every_days: int = 1

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(self._universe)

    def _weights(self, history: DailyHistory) -> Dict[str, float]:  # pragma: no cover - abstract-ish
        raise NotImplementedError

    def run(
        self,
        bars_by_symbol: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
        initial_capital: float,
    ) -> List[Dict[str, Any]]:
        symbols = self.required_symbols()
        subset = {s: bars_by_symbol[s] for s in symbols if s in bars_by_symbol}
        if not subset:
            return []

        last: Dict[str, float] = {}

        def weight_fn(history: DailyHistory, cur_date, day_index):
            nonlocal last
            last = self._weights(history)
            return last

        curve, n_trades = run_daily_signal_strategy(
            subset, start_date, end_date, initial_capital, weight_fn,
            rebalance_every_days=self._rebalance_every_days,
        )
        self._num_trades = n_trades
        return curve

    def num_trades(self) -> int:
        return getattr(self, "_num_trades", 0)

    def decide(self, history: DailyHistory) -> Dict[str, float]:
        return self._weights(history)
