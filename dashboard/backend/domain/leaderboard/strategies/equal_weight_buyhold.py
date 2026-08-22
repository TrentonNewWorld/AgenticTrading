"""Equal-weight buy & hold across a universe (default DJIA 30).

Allocates equal dollars to each symbol at the start and holds — weights drift
with price, unlike the continuously-rebalanced equal-weight index.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from dashboard.backend.baseline_generator import BaselineGenerator
from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._common import subset_bars
from ._signal_engine import DailyHistory, load_strategy_state, save_strategy_state


class EqualWeightBuyHoldStrategy(BaselineStrategy):
    key = "equal_weight_buyhold"

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def run(
        self,
        bars_by_symbol: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
        initial_capital: float,
    ) -> List[Dict[str, Any]]:
        symbols = self.required_symbols()
        bars_subset = subset_bars(bars_by_symbol, symbols)
        if not bars_subset:
            return []
        return BaselineGenerator().generate_buyhold_baseline(
            bars_subset, start_date, end_date, initial_capital, symbols
        )

    def num_trades(self) -> int:
        return len(self.required_symbols())

    def decide(self, history: DailyHistory) -> Optional[Dict[str, float]]:
        """Live/paper-trading entrypoint. Unlike the 12 signal strategies,
        "buy and hold" means never rebalance once bought -- so after the first
        call this deliberately returns ``None`` ("make no trades this cycle"),
        not ``{}`` (which the paper runner reads as "go to 100% cash"). A
        strategy that wants a periodic no-op MUST use ``None`` for that,
        distinct from an intentional flat/all-cash signal."""
        state = load_strategy_state(self.key)
        if state.get("established"):
            return None
        symbols = self.required_symbols()
        valid = [s for s in symbols if s in history.close.columns and pd.notna(history.close[s].iloc[-1])]
        if not valid:
            return {}
        save_strategy_state(self.key, {"established": True})
        return {s: 1.0 / len(valid) for s in valid}
