"""LLM-agent-driven Futures Strategy Catalog entry -- the futures counterpart
to domain/leaderboard/strategies/llm_agent.py. Mirrors
domain/crypto/strategies/llm_agent.py exactly. A My Agents futures agent that
tests well converts into one of these (domain/futures/catalog.py's
convert_agent_to_strategy) instead of sandboxed code: the stored
strategy_prompt is replayed through the LLM at every catalog compute, via the
same day-by-day loop (domain/futures/backtester.py's run_llm_agent_backtest)
the agent's own backtest already used.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dashboard.backend.infrastructure.market_data.yahoo_futures import FUTURES_UNIVERSE

from .base import FuturesBaselineStrategy


class LLMAgentFuturesStrategy(FuturesBaselineStrategy):
    key = "fut_llm_agent"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.strategy_prompt = (self.config.get("strategy_prompt") or "").strip()
        self.model_id = self.config.get("model")
        self._num_trades = 0

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(FUTURES_UNIVERSE)

    def code(self) -> Optional[str]:
        return None

    def run(self, start_date: str, end_date: str, initial_capital: float) -> List[Dict[str, Any]]:
        from datetime import date as date_cls

        from dashboard.backend.domain.futures.backtester import run_llm_agent_backtest

        start = date_cls.fromisoformat(start_date)
        end = date_cls.fromisoformat(end_date)
        return run_llm_agent_backtest(
            self.strategy_prompt, self.model_id, self.required_symbols(), start, end, initial_capital,
        )

    def num_trades(self) -> int:
        return self._num_trades
