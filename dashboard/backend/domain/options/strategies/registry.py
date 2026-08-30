"""Registry mapping config ``strategy`` keys to Options baseline strategy
classes.

Sub-phase 8 of the Options-dashboard plan. Own registry, not merged into
``domain.leaderboard.strategies``'s -- see this package's ``__init__.py``.
To add a new Options starter: create a module here with an
``OptionsBaselineStrategy`` subclass, then register its class below.
"""

from __future__ import annotations

from typing import Any, Dict, Type

from .base import OptionsBaselineStrategy
from .cash_secured_put import CashSecuredPutStrategy
from .covered_call_starter import CoveredCallStarterStrategy
from .llm_agent import LLMAgentOptionsStrategy
from .long_call_momentum import LongCallMomentumStrategy
from .sandboxed import SandboxedOptionsStrategy

_STRATEGY_CLASSES = [
    CoveredCallStarterStrategy,
    CashSecuredPutStrategy,
    LongCallMomentumStrategy,
    SandboxedOptionsStrategy,
    LLMAgentOptionsStrategy,
]

_REGISTRY: Dict[str, Type[OptionsBaselineStrategy]] = {cls.key: cls for cls in _STRATEGY_CLASSES}


def get_strategy(config: Dict[str, Any]) -> OptionsBaselineStrategy:
    """Instantiate the strategy for an Options leaderboard config entry."""
    key = config.get("strategy") or config.get("id")
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown Options baseline strategy '{key}'. Available: {sorted(_REGISTRY)}"
        )
    return cls(config)


def available_strategies() -> Dict[str, Type[OptionsBaselineStrategy]]:
    return dict(_REGISTRY)
