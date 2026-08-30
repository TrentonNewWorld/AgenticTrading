"""Registry mapping config ``strategy`` keys to Crypto baseline strategy
classes. Mirrors domain/futures/strategies/registry.py and
domain/forex/strategies/registry.py.
"""

from __future__ import annotations

from typing import Any, Dict, Type

from .base import CryptoBaselineStrategy
from .dip_reversion import DipReversionStrategy
from .donchian_breakout import DonchianBreakoutStrategy
from .ema_ribbon import EmaRibbonStrategy
from .llm_agent import LLMAgentCryptoStrategy
from .momentum_basket import MomentumBasketStrategy
from .multiday_momentum import MultidayMomentumStrategy
from .rsi_reversion import RsiReversionStrategy
from .sandboxed import SandboxedCryptoStrategy
from .sma_cross import SmaCrossStrategy
from .trend_dip import TrendDipStrategy
from .vol_breakout import VolBreakoutStrategy
from .zscore_meanrev import ZscoreMeanrevStrategy

_STRATEGY_CLASSES = [
    MomentumBasketStrategy,
    DipReversionStrategy,
    SmaCrossStrategy,
    RsiReversionStrategy,
    DonchianBreakoutStrategy,
    ZscoreMeanrevStrategy,
    EmaRibbonStrategy,
    VolBreakoutStrategy,
    TrendDipStrategy,
    MultidayMomentumStrategy,
    SandboxedCryptoStrategy,
    LLMAgentCryptoStrategy,
]

_REGISTRY: Dict[str, Type[CryptoBaselineStrategy]] = {cls.key: cls for cls in _STRATEGY_CLASSES}


def get_strategy(config: Dict[str, Any]) -> CryptoBaselineStrategy:
    key = config.get("strategy") or config.get("id")
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(f"Unknown Crypto baseline strategy '{key}'. Available: {sorted(_REGISTRY)}")
    return cls(config)


def available_strategies() -> Dict[str, Type[CryptoBaselineStrategy]]:
    return dict(_REGISTRY)
