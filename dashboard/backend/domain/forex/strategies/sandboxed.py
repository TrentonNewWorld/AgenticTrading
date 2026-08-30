"""Wraps a Testing-page-approved forex upload so it plugs into the same
Forex Strategy Catalog machinery every starter strategy uses. Mirrors
domain/futures/strategies/sandboxed.py exactly.
"""

from __future__ import annotations

from typing import Any, Dict, List

from dashboard.backend.infrastructure.market_data.yahoo_forex import FOREX_UNIVERSE

from .base import ForexBaselineStrategy


class SandboxedForexStrategy(ForexBaselineStrategy):
    key = "fx_sandboxed"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._code = self.config.get("code") or ""

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(FOREX_UNIVERSE)

    def code(self) -> str:
        return self._code
