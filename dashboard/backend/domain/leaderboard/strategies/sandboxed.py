"""Wraps a Testing-page-approved upload so it plugs into the same Strategy
Catalog / live / paper machinery every built-in baseline uses.

The strategy's Python source lives inline in its ``leaderboard.json`` entry
(``config["code"]``) -- written once, at "Add to strategies list" time, by
``domain.strategy_testing``. Every run of this class re-executes that source
through the exact same AST-checked, subprocess-isolated sandbox
(``domain.manual10.sandbox.run_decide``) as the Testing page's own scan and
backtest used, and as Manual's uploaded strategies use for live paper
trading -- one execution boundary for every path a piece of uploaded code
can take through this app.
"""

from __future__ import annotations

from datetime import date as date_cls
from typing import Any, Dict, List

import pandas as pd

from dashboard.backend.domain.strategy_testing.simulator import simulate
from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy


class SandboxedStrategy(BaselineStrategy):
    key = "sandboxed"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.code = self.config.get("code") or ""
        self._num_trades = 0

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def run(
        self,
        bars_by_symbol: Dict[str, "pd.DataFrame"],
        start_date: str,
        end_date: str,
        initial_capital: float,
    ) -> List[Dict[str, Any]]:
        if not self.code or not bars_by_symbol:
            return []
        start = date_cls.fromisoformat(start_date)
        end = date_cls.fromisoformat(end_date)
        all_dates = sorted({idx.date() for df in bars_by_symbol.values() for idx in df.index})
        test_dates = [d for d in all_dates if start <= d <= end]
        if not test_dates:
            return []
        curve = simulate(self.code, bars_by_symbol, test_dates, initial_capital)
        return [{"timestamp": row["date"], "equity": row["equity"]} for row in curve]

    def decide(self, history) -> Dict[str, float]:
        """Live/paper entrypoint -- one sandboxed decide() call against the
        most recent daily close, same contract as every other strategy's
        decide()."""
        from dashboard.backend.domain.manual10.sandbox import run_decide

        symbols = self.required_symbols()
        price_history: Dict[str, List[Dict[str, Any]]] = {}
        if history.close.empty:
            return {}
        for symbol in symbols:
            if symbol not in history.close.columns:
                continue
            series = history.close[symbol].dropna()
            if series.empty:
                continue
            price_history[symbol] = [{"t": str(idx.date()), "close": float(v)} for idx, v in series.items()]
        if not price_history:
            return {}
        return run_decide(self.code, price_history, timeout=10)

    def num_trades(self) -> int:
        return self._num_trades
