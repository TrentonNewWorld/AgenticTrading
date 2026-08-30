"""Wraps a Testing-page-approved options upload so it plugs into the same
Options Strategy Catalog machinery every starter strategy uses.

Mirrors ``domain/leaderboard/strategies/sandboxed.py``'s role for the stocks
catalog: the strategy's ``decide_options()`` source lives inline in its
``leaderboard_options.json`` entry (``config["code"]``), written once at
"Add to strategies list" time by ``domain.options.catalog.add_to_catalog``.
Every run of this class re-executes that exact source through
``domain.options.backtester.run_backtest`` (via the shared
``OptionsBaselineStrategy.run()``) -- the same engine every starter strategy
and every uploaded strategy's own Testing-page backtest already goes
through.
"""

from __future__ import annotations

from typing import Any, Dict, List

from dashboard.backend.domain.options.engine import DEFAULT_OPTIONS_UNIVERSE

from .base import OptionsBaselineStrategy


class SandboxedOptionsStrategy(OptionsBaselineStrategy):
    key = "opt_sandboxed"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._code = self.config.get("code") or ""

    def required_underlyings(self) -> List[str]:
        underlyings = self.config.get("underlyings")
        return list(underlyings) if underlyings else list(DEFAULT_OPTIONS_UNIVERSE)

    def code(self) -> str:
        return self._code
