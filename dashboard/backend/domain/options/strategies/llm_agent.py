"""LLM-agent-driven Options Strategy Catalog entry -- the options counterpart
to domain/leaderboard/strategies/llm_agent.py. A My Agents options agent that
tests well converts into one of these (domain/options/catalog.py's
convert_agent_to_strategy) instead of sandboxed code: the stored
strategy_prompt is replayed through the LLM at every catalog compute, via the
same day-by-day loop (domain/options/backtester.py's run_llm_agent_backtest)
the agent's own backtest already used.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dashboard.backend.infrastructure.market_data.alpaca_options import OPTIONS_UNDERLYING_UNIVERSE

from .base import OptionsBaselineStrategy


class LLMAgentOptionsStrategy(OptionsBaselineStrategy):
    key = "opt_llm_agent"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.strategy_prompt = (self.config.get("strategy_prompt") or "").strip()
        self.model_id = self.config.get("model")
        self._num_trades = 0

    def required_underlyings(self) -> List[str]:
        underlyings = self.config.get("underlyings")
        return list(underlyings) if underlyings else list(OPTIONS_UNDERLYING_UNIVERSE)

    def code(self) -> Optional[str]:
        return None

    def run(self, start_date: str, end_date: str, initial_capital: float) -> List[Dict[str, Any]]:
        from datetime import date as date_cls

        from dashboard.backend.domain.options.backtester import run_llm_agent_backtest

        start = date_cls.fromisoformat(start_date)
        end = date_cls.fromisoformat(end_date)
        return run_llm_agent_backtest(
            self.strategy_prompt, self.model_id, self.required_underlyings(), start, end, initial_capital,
        )

    def num_trades(self) -> int:
        return self._num_trades
