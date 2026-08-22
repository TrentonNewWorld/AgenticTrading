"""PatternRecognition, from the freqtrade-strategies community repository
(github.com/freqtrade/freqtrade-strategies, user_data/strategies
/PatternRecognition.py).

Buys when a stock forms a 'high wave' candle (a small price-change body with
long shadows both above and below, signaling indecision) at a fresh 10-day
low. TA-Lib's CDLHIGHWAVE definition is reproduced directly from its OHLC
ratios rather than depending on the TA-Lib C library. The original strategy
has no sell logic of its own (it relies on an ROI table/stoploss not modeled
here), so a fixed 10-trading-day hold is used as the exit -- a default this
strategy needed and the source didn't specify.

Lookback: needs ~10 days for the rolling low; below that, holds whatever is
already held and takes no new positions.
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

_MAX_POSITIONS = 8
_MIN_HISTORY = 10
_HOLD_DAYS = 10


class PatternRecognitionStrategy(BaselineStrategy):
    key = "pattern_recognition"

    def __init__(self, config):
        super().__init__(config)
        #: {symbol: ISO date string of the entry day}. Keyed by calendar date
        #: rather than a row-count index so it round-trips correctly through
        #: `decide()`'s persisted state across separate process runs -- a
        #: relative index would mean something different every time the
        #: history grows.
        self._entry_day: Dict[str, str] = {}

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _entry(self, history: DailyHistory, sym: str) -> bool:
        close = history.close[sym].dropna()
        if len(close) < _MIN_HISTORY:
            return False
        open_ = history.open[sym].reindex(close.index)
        high = history.high[sym].reindex(close.index)
        low = history.low[sym].reindex(close.index)
        rng = high.iloc[-1] - low.iloc[-1]
        if not rng:
            return False
        body = abs(close.iloc[-1] - open_.iloc[-1]) / rng
        upper_shadow = (high.iloc[-1] - max(open_.iloc[-1], close.iloc[-1])) / rng
        lower_shadow = (min(open_.iloc[-1], close.iloc[-1]) - low.iloc[-1]) / rng
        is_high_wave = body < 0.3 and upper_shadow > 0.3 and lower_shadow > 0.3
        at_recent_low = close.iloc[-1] <= close.iloc[-_MIN_HISTORY:].min() * 1.01
        signal = bool(is_high_wave and at_recent_low)
        if signal:
            self._entry_day[sym] = str(close.index[-1].date())
        return signal

    def _exit(self, history: DailyHistory, sym: str) -> bool:
        close = history.close[sym].dropna()
        held_since = self._entry_day.get(sym)
        if held_since is None:
            return False
        days_held = int((close.index > pd.Timestamp(held_since)).sum())
        return days_held >= _HOLD_DAYS

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

        self._entry_day = {}
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
        """Live/paper-trading entrypoint. Both the held-position set and the
        per-symbol entry-day map must survive across separate process runs."""
        symbols = self.required_symbols()
        state = load_strategy_state(self.key)
        self._entry_day = dict(state.get("entry_day") or {})
        weight_fn = make_entry_exit_weight_fn(
            self._entry, self._exit, symbols, _MAX_POSITIONS, _MIN_HISTORY,
            initial_held=state.get("held"),
        )
        weights = decide_live(weight_fn, history)
        save_strategy_state(self.key, {"held": sorted(weight_fn.held), "entry_day": self._entry_day})
        return weights
