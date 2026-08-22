"""Deterministic reading of the Marketplace "Blue-Chip Steady" template
(``dashboard/config/marketplace.json``).

Picks the 8 Dow stocks with the best trailing ~1-year return once, holds
them equally weighted, and only sells a name entirely if it falls 25% from
its entry price. Validated in the "Strategy Lab" backtest report.

State: the initial picks and each pick's entry price must survive both a
single ``run()`` backtest (picked once at the start of the window) and
separate ``decide()`` invocations in live/paper trading (a fresh process each
time) -- persisted via ``load_strategy_state``/``save_strategy_state``, the
same mechanism ``pattern_recognition.py`` uses for its per-symbol entry-day
map.

Lookback caveat: wants 220 days of history to rank the initial picks; below
that, ranks over whatever history is available.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._signal_engine import (
    DailyHistory,
    decide_live,
    load_strategy_state,
    run_daily_signal_strategy,
    save_strategy_state,
)

_TOP_N = 8
_STOP_LOSS = -0.25
_REBALANCE_DAYS = 252


class BlueChipSteadyStrategy(BaselineStrategy):
    key = "blue_chip_steady"

    def __init__(self, config):
        super().__init__(config)
        self._picks: Optional[List[str]] = None
        self._entry_px: Dict[str, float] = {}
        self._stopped_out: Set[str] = set()

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _pick_once(self, history: DailyHistory) -> None:
        close = history.close
        offset = min(220, len(history) - 1)
        trailing = close.iloc[-1] / close.iloc[-1 - offset] - 1 if offset > 0 else close.iloc[-1] * 0
        picks = trailing.dropna().sort_values(ascending=False).head(_TOP_N).index.tolist()
        self._picks = picks
        self._entry_px = {s: float(close.iloc[-1][s]) for s in picks}
        self._stopped_out = set()

    def _weight_fn(self, history: DailyHistory, cur_date, day_index) -> Dict[str, float]:
        if history.close.empty:
            return {}
        if self._picks is None:
            self._pick_once(history)
        close = history.close.iloc[-1]
        active = [s for s in self._picks if s not in self._stopped_out]
        for s in list(active):
            px = close.get(s)
            entry = self._entry_px.get(s)
            if px is not None and entry and px / entry - 1 < _STOP_LOSS:
                self._stopped_out.add(s)
        active = [s for s in self._picks if s not in self._stopped_out]
        if not active:
            return {}
        return {s: 1.0 / len(active) for s in active}

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

        self._picks = None
        self._entry_px = {}
        self._stopped_out = set()
        curve, n_trades = run_daily_signal_strategy(
            bars_subset, start_date, end_date, initial_capital, self._weight_fn,
            rebalance_every_days=_REBALANCE_DAYS,
        )
        self._num_trades = n_trades
        return curve

    def num_trades(self) -> int:
        return getattr(self, "_num_trades", 0)

    def decide(self, history: DailyHistory) -> Dict[str, float]:
        state = load_strategy_state(self.key)
        self._picks = state.get("picks")
        self._entry_px = dict(state.get("entry_px") or {})
        self._stopped_out = set(state.get("stopped_out") or ())
        weights = decide_live(self._weight_fn, history)
        save_strategy_state(
            self.key,
            {"picks": self._picks, "entry_px": self._entry_px, "stopped_out": sorted(self._stopped_out)},
        )
        return weights
