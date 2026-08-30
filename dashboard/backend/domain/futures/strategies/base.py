"""Base class for Futures Strategy Catalog starter strategies.

Parallel to domain/options/strategies/base.py -- every concrete strategy
here is just a decide_futures() source string (the exact same sandbox
contract an uploaded strategy uses) plus a declared symbol universe; run()
is one shared implementation delegating to domain.futures.backtester.
run_backtest, so a starter strategy's logic runs through the identical
sandboxed path a user's own upload would.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date as date_cls
from typing import Any, Dict, List


class FuturesBaselineStrategy(ABC):
    """A single Futures Strategy Catalog starter strategy."""

    key: str = ""

    PARAM_SCHEMA: Dict[str, Dict[str, Any]] = {}

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.id = self.config.get("id")
        self.name = self.config.get("name")

    @abstractmethod
    def required_symbols(self) -> List[str]:
        """Futures symbols this strategy trades."""

    @abstractmethod
    def code(self) -> str:
        """This strategy's decide_futures() source -- run through the exact
        same sandbox (domain.futures.sandbox) and backtester
        (domain.futures.backtester) an uploaded strategy uses."""

    def run(self, start_date: str, end_date: str, initial_capital: float) -> List[Dict[str, Any]]:
        from dashboard.backend.domain.futures.backtester import run_backtest

        start = date_cls.fromisoformat(start_date)
        end = date_cls.fromisoformat(end_date)
        return run_backtest(self.code(), self.required_symbols(), start, end, initial_capital)

    def num_trades(self) -> int:
        return 0
