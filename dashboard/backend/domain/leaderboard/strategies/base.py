"""Base class for leaderboard baseline strategies.

Each baseline strategy lives in its own module and subclasses ``BaselineStrategy``.
Strategies are independent: a bug in one must never affect another. Shared,
side-effect-free helpers live in ``_common.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

import pandas as pd


class BaselineStrategy(ABC):
    """A single leaderboard baseline strategy.

    Subclasses declare a class-level ``key`` (used by the registry / config
    ``strategy`` field) and implement ``required_symbols`` and ``run``.
    """

    key: str = ""

    #: Constants this strategy exposes as real, tunable parameters on the
    #: Strategy Catalog's Edit page -- e.g. {"top_n": {"label": "Positions
    #: held", "type": "int", "default": 10, "min": 1, "max": 30}}. Empty by
    #: default: a strategy with nothing here (mean_variance_djia,
    #: equal_weight_djia, spy_index, djia_index, ...) has no editable
    #: parameters, only a description. See strategy_overrides.py.
    PARAM_SCHEMA: Dict[str, Dict[str, Any]] = {}

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.id = self.config.get("id")
        self.name = self.config.get("name")

    @abstractmethod
    def required_symbols(self) -> List[str]:
        """Symbols this strategy needs market data for."""

    @abstractmethod
    def run(
        self,
        bars_by_symbol: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
        initial_capital: float,
    ) -> List[Dict[str, Any]]:
        """Return an hourly equity curve: [{timestamp, equity, cash, positions_value}, ...]."""

    def num_trades(self) -> int:
        """Number of trades this strategy executes (for display only)."""
        return 0
