"""Base class for Forex Strategy Catalog starter strategies. Mirrors
domain/futures/strategies/base.py exactly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date as date_cls
from typing import Any, Dict, List


class ForexBaselineStrategy(ABC):
    """A single Forex Strategy Catalog starter strategy."""

    key: str = ""

    PARAM_SCHEMA: Dict[str, Dict[str, Any]] = {}

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.id = self.config.get("id")
        self.name = self.config.get("name")

    @abstractmethod
    def required_symbols(self) -> List[str]:
        """Forex pairs this strategy trades."""

    @abstractmethod
    def code(self) -> str:
        """This strategy's decide_forex() source -- run through the exact
        same sandbox (domain.forex.sandbox) and backtester
        (domain.forex.backtester) an uploaded strategy uses."""

    def run(self, start_date: str, end_date: str, initial_capital: float) -> List[Dict[str, Any]]:
        from dashboard.backend.domain.forex.backtester import run_backtest

        start = date_cls.fromisoformat(start_date)
        end = date_cls.fromisoformat(end_date)
        return run_backtest(self.code(), self.required_symbols(), start, end, initial_capital)

    def num_trades(self) -> int:
        return 0
