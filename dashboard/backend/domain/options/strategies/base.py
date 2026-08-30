"""Base class for Options Strategy Catalog starter strategies.

Sub-phase 8 of the Options-dashboard plan. Parallel to
``domain/leaderboard/strategies/base.py``'s ``BaselineStrategy``, but
chain/contract-aware: instead of a ``run(bars_by_symbol, ...)`` method that
each subclass implements independently, every concrete strategy here is
just a ``decide_options()`` source string (the exact same sandbox contract
an uploaded strategy uses) plus a declared underlying universe -- ``run()``
itself is one shared implementation delegating to
``domain.options.backtester.run_backtest``, the same battle-tested engine
Sub-phase 6 already verified. This keeps a starter strategy's real trading
logic running through the identical sandboxed path a user's own upload
would, so there is exactly one execution engine for Options code, not two.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date as date_cls
from typing import Any, Dict, List


class OptionsBaselineStrategy(ABC):
    """A single Options Strategy Catalog starter strategy."""

    key: str = ""

    #: Same convention as BaselineStrategy.PARAM_SCHEMA -- empty by default,
    #: no starter strategy here exposes tunable parameters in v1.
    PARAM_SCHEMA: Dict[str, Dict[str, Any]] = {}

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.id = self.config.get("id")
        self.name = self.config.get("name")

    @abstractmethod
    def required_underlyings(self) -> List[str]:
        """Underlyings this strategy needs an options chain for."""

    @abstractmethod
    def code(self) -> str:
        """This strategy's decide_options() source -- run through the exact
        same sandbox (domain.options.sandbox) and backtester
        (domain.options.backtester) an uploaded strategy uses."""

    def run(self, start_date: str, end_date: str, initial_capital: float) -> List[Dict[str, Any]]:
        """Returns [{"date": "YYYY-MM-DD", "equity": float}, ...]."""
        from dashboard.backend.domain.options.backtester import run_backtest

        start = date_cls.fromisoformat(start_date)
        end = date_cls.fromisoformat(end_date)
        return run_backtest(self.code(), self.required_underlyings(), start, end, initial_capital)

    def num_trades(self) -> int:
        """Number of trades this strategy executes (for display only)."""
        return 0
