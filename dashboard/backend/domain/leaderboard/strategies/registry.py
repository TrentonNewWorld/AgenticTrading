"""Registry mapping config ``strategy`` keys to baseline strategy classes.

To add a new baseline: create a module in this package with a ``BaselineStrategy``
subclass, then register its class below. Nothing else needs to change.
"""

from __future__ import annotations

from typing import Any, Dict, Type

from .base import BaselineStrategy
from .buy_hold import BuyHoldStrategy
from .equal_weight_buyhold import EqualWeightBuyHoldStrategy
from .equal_weight_index import EqualWeightIndexStrategy
from .llm_agent import LLMAgentStrategy
from .market_index import MarketIndexStrategy
from .mean_variance import MeanVarianceStrategy

# External strategies (Strategy Lab catalog: TradingAgents / QuantConnect /
# freqtrade), added as deterministic baselines -- none involve an LLM call.
from .tradingagents_composite import TradingAgentsCompositeStrategy
from .capm_alpha_ranking import CAPMAlphaRankingStrategy
from .momentum_effect import MomentumEffectStrategy
from .volatility_effect import VolatilityEffectStrategy
from .short_term_reversal import ShortTermReversalStrategy
from .overnight_anomaly import OvernightAnomalyStrategy
from .turn_of_month import TurnOfMonthStrategy
from .bandtastic import BandtasticStrategy
from .supertrend_triple import SupertrendTripleStrategy
from .hlhb import HlhbStrategy
from .trendrider import TrendRiderStrategy
from .pattern_recognition import PatternRecognitionStrategy
from .universal_macd import UniversalMACDStrategy
from .almgren_chriss_twap import AlmgrenChrissTwapStrategy

# Marketplace templates (Strategy Lab catalog), translated by hand from their
# natural-language trading instructions into explicit deterministic rules --
# the templates themselves are LLM prompts, not numeric formulas.
from .balanced_starter import BalancedStarterStrategy
from .momentum_scout import MomentumScoutStrategy
from .three_step_analyst import ThreeStepAnalystStrategy
from .ai_hedge_fund import AIHedgeFundStrategy
from .blue_chip_steady import BlueChipSteadyStrategy
from .even_split_dow import EvenSplitDowStrategy
from .contrarian_dip_buyer import ContrarianDipBuyerStrategy
from .sector_rotator import SectorRotatorStrategy
from .volatility_guard import VolatilityGuardStrategy

# User-uploaded strategies added via the Testing page (upload -> scan ->
# backtest -> "Add to strategies list"); source code stored inline in the
# leaderboard.json entry and re-run through the same sandbox every time.
from .sandboxed import SandboxedStrategy
from .tv_rsi2_connors import TvRsi2ConnorsStrategy
from .tv_golden_cross import TvGoldenCrossStrategy
from .tv_donchian_turtle import TvDonchianTurtleStrategy
from .tv_bollinger_meanrev import TvBollingerMeanrevStrategy
from .tv_ichimoku import TvIchimokuStrategy
from .tv_stochastic import TvStochasticStrategy
from .tv_williams_r import TvWilliamsRStrategy
from .tv_cci_100 import TvCci100Strategy
from .tv_adx_dmi import TvAdxDmiStrategy
from .tv_52w_high import Tv52wHighStrategy
from .tv_dual_momentum import TvDualMomentumStrategy
from .tv_ema_ribbon import TvEmaRibbonStrategy

_STRATEGY_CLASSES = [
    BuyHoldStrategy,
    EqualWeightIndexStrategy,
    EqualWeightBuyHoldStrategy,
    MarketIndexStrategy,
    MeanVarianceStrategy,
    LLMAgentStrategy,
    TradingAgentsCompositeStrategy,
    CAPMAlphaRankingStrategy,
    MomentumEffectStrategy,
    VolatilityEffectStrategy,
    ShortTermReversalStrategy,
    OvernightAnomalyStrategy,
    TurnOfMonthStrategy,
    BandtasticStrategy,
    SupertrendTripleStrategy,
    HlhbStrategy,
    TrendRiderStrategy,
    PatternRecognitionStrategy,
    UniversalMACDStrategy,
    AlmgrenChrissTwapStrategy,
    BalancedStarterStrategy,
    MomentumScoutStrategy,
    ThreeStepAnalystStrategy,
    AIHedgeFundStrategy,
    BlueChipSteadyStrategy,
    EvenSplitDowStrategy,
    ContrarianDipBuyerStrategy,
    SectorRotatorStrategy,
    VolatilityGuardStrategy,
    SandboxedStrategy,
    TvRsi2ConnorsStrategy,
    TvGoldenCrossStrategy,
    TvDonchianTurtleStrategy,
    TvBollingerMeanrevStrategy,
    TvIchimokuStrategy,
    TvStochasticStrategy,
    TvWilliamsRStrategy,
    TvCci100Strategy,
    TvAdxDmiStrategy,
    Tv52wHighStrategy,
    TvDualMomentumStrategy,
    TvEmaRibbonStrategy,
]

_REGISTRY: Dict[str, Type[BaselineStrategy]] = {cls.key: cls for cls in _STRATEGY_CLASSES}

# Backward-compatible aliases for the original config ``type`` values.
_ALIASES = {
    "index": "equal_weight_index",
    "buy_hold": "buy_hold",
}


def get_strategy(config: Dict[str, Any]) -> BaselineStrategy:
    """Instantiate the strategy for a leaderboard config entry."""
    key = config.get("strategy") or config.get("type")
    if key in _ALIASES:
        key = _ALIASES[key]
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown baseline strategy '{key}'. Available: {sorted(_REGISTRY)}"
        )
    return cls(config)


def available_strategies() -> Dict[str, Type[BaselineStrategy]]:
    return dict(_REGISTRY)
