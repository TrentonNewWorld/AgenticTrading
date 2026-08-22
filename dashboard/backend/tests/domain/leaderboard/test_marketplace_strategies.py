"""Tests for the 9 Marketplace-template strategies added to the leaderboard
baseline registry (Balanced Starter, Momentum Scout, Three-Step Analyst, AI
Hedge Fund, Blue-Chip Steady, Even-Split Dow, Contrarian Dip Buyer, Sector
Rotator, Volatility Guard) -- hand-translated from ``dashboard/config
/marketplace.json``'s natural-language prompts into explicit deterministic
rules, per the "Strategy Lab" backtest report. Mirrors
``test_external_strategies.py``'s pattern.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dashboard.backend.domain.leaderboard import strategies as canon
from dashboard.backend.domain.leaderboard.strategies.ai_hedge_fund import AIHedgeFundStrategy
from dashboard.backend.domain.leaderboard.strategies.balanced_starter import BalancedStarterStrategy
from dashboard.backend.domain.leaderboard.strategies.blue_chip_steady import BlueChipSteadyStrategy
from dashboard.backend.domain.leaderboard.strategies.contrarian_dip_buyer import ContrarianDipBuyerStrategy
from dashboard.backend.domain.leaderboard.strategies.even_split_dow import EvenSplitDowStrategy
from dashboard.backend.domain.leaderboard.strategies.momentum_scout import MomentumScoutStrategy
from dashboard.backend.domain.leaderboard.strategies.sector_rotator import SectorRotatorStrategy
from dashboard.backend.domain.leaderboard.strategies.three_step_analyst import ThreeStepAnalystStrategy
from dashboard.backend.domain.leaderboard.strategies.volatility_guard import VolatilityGuardStrategy

_MARKETPLACE_KEYS_TO_CLASSES = {
    "balanced_starter": BalancedStarterStrategy,
    "momentum_scout": MomentumScoutStrategy,
    "three_step_analyst": ThreeStepAnalystStrategy,
    "ai_hedge_fund": AIHedgeFundStrategy,
    "blue_chip_steady": BlueChipSteadyStrategy,
    "even_split_dow": EvenSplitDowStrategy,
    "contrarian_dip_buyer": ContrarianDipBuyerStrategy,
    "sector_rotator": SectorRotatorStrategy,
    "volatility_guard": VolatilityGuardStrategy,
}


# ---------------------------------------------------------------------------
# Registry identity + resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,cls", _MARKETPLACE_KEYS_TO_CLASSES.items())
def test_registry_identity(key, cls):
    registry = canon.available_strategies()
    assert registry[key] is cls


@pytest.mark.parametrize("key,cls", _MARKETPLACE_KEYS_TO_CLASSES.items())
def test_get_strategy_resolves_by_key(key, cls):
    strat = canon.get_strategy({"id": "x", "name": "X", "strategy": key})
    assert isinstance(strat, cls)
    assert strat.id == "x"
    assert strat.name == "X"


@pytest.mark.parametrize("key,cls", _MARKETPLACE_KEYS_TO_CLASSES.items())
def test_required_symbols_defaults_and_overrides(key, cls):
    strat = cls({})
    default = strat.required_symbols()
    assert len(default) == 30
    custom = cls({"symbols": ["AAPL", "MSFT"]}).required_symbols()
    assert custom == ["AAPL", "MSFT"]


@pytest.mark.parametrize("key,cls", _MARKETPLACE_KEYS_TO_CLASSES.items())
def test_every_strategy_defines_its_own_decide(key, cls):
    """Every marketplace strategy must support live/paper trading, same as
    the 14 external strategies -- decide() is what makes a strategy
    selectable via `run_alpaca_paper_strategy.py --strategy <key>`."""
    assert "decide" in cls.__dict__


# ---------------------------------------------------------------------------
# run() smoke tests against small synthetic hourly bars
# ---------------------------------------------------------------------------

_ET_HOURS = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30", "16:00"]


def _hourly_index(n_days: int, start: str = "2026-01-05") -> pd.DatetimeIndex:
    timestamps = []
    day = pd.Timestamp(start, tz="US/Eastern")
    added = 0
    while added < n_days:
        if day.weekday() < 5:
            for hhmm in _ET_HOURS:
                h, m = map(int, hhmm.split(":"))
                timestamps.append(day.replace(hour=h, minute=m).tz_convert("UTC"))
            added += 1
        day = day + pd.Timedelta(days=1)
    return pd.DatetimeIndex(timestamps)


def _synthetic_bars(n_days: int, seed: int, base_price: float = 100.0) -> pd.DataFrame:
    idx = _hourly_index(n_days)
    rng = np.random.default_rng(seed)
    n = len(idx)
    rets = rng.normal(loc=0.0003, scale=0.004, size=n)
    close = base_price * np.cumprod(1 + rets)
    open_ = np.concatenate([[base_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.002, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.002, n))
    volume = rng.integers(1_000, 10_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


@pytest.fixture(scope="module")
def bars_30_symbols():
    symbols = [f"SYM{i}" for i in range(30)]
    return {sym: _synthetic_bars(300, seed=i) for i, sym in enumerate(symbols)}


def _assert_valid_curve(curve):
    assert isinstance(curve, list)
    if not curve:
        return
    for row in curve:
        assert set(row) >= {"timestamp", "equity", "cash", "positions_value", "daily_return"}
        assert np.isfinite(row["equity"])
        assert row["equity"] >= 0


@pytest.mark.parametrize("key,cls", _MARKETPLACE_KEYS_TO_CLASSES.items())
def test_run_produces_valid_curve(key, cls, bars_30_symbols):
    strat = cls({})
    # Use the trailing 60 trading days as the test window so the longer
    # lookbacks (Blue-Chip Steady's 220-day pick, AI Hedge Fund's 252-day
    # momentum) have real reference history behind them, matching how the
    # leaderboard's own contest window has a reference buffer before it.
    dates = sorted({ts.date() for ts in bars_30_symbols["SYM0"].index})
    start = dates[-60].isoformat()
    end = dates[-1].isoformat()
    curve = strat.run(bars_30_symbols, start, end, 100_000.0)
    _assert_valid_curve(curve)
    assert strat.num_trades() >= 0


def test_run_with_empty_bars_returns_empty_list():
    for cls in _MARKETPLACE_KEYS_TO_CLASSES.values():
        strat = cls({})
        assert strat.run({}, "2026-01-05", "2026-01-06", 100_000.0) == []


def test_run_with_insufficient_history_does_not_crash(bars_30_symbols):
    """A contest window shorter than a strategy's desired lookback must
    degrade gracefully, never raise or NaN out equity -- same guarantee the
    14 external strategies carry."""
    short_bars = {sym: df.iloc[:8] for sym, df in bars_30_symbols.items()}  # 1 trading day
    start = short_bars["SYM0"].index[0].date().isoformat()
    end = short_bars["SYM0"].index[-1].date().isoformat()
    for key, cls in _MARKETPLACE_KEYS_TO_CLASSES.items():
        strat = cls({})
        curve = strat.run(short_bars, start, end, 100_000.0)
        _assert_valid_curve(curve)


# ---------------------------------------------------------------------------
# decide() (live/paper-trading entrypoint)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,cls", _MARKETPLACE_KEYS_TO_CLASSES.items())
def test_decide_returns_a_weight_dict(key, cls, bars_30_symbols, tmp_path, monkeypatch):
    from dashboard.backend.domain.leaderboard.strategies import _signal_engine

    monkeypatch.setattr(_signal_engine, "LIVE_STATE_DIR", tmp_path)
    strat = cls({})
    history = _signal_engine.daily_history(bars_30_symbols)
    weights = strat.decide(history)
    assert isinstance(weights, dict)
    assert all(isinstance(v, (int, float)) for v in weights.values())
    if weights:
        assert abs(sum(weights.values()) - 1.0) < 1e-6 or sum(weights.values()) <= 1.0 + 1e-9


@pytest.mark.parametrize(
    "key,cls",
    [(k, c) for k, c in _MARKETPLACE_KEYS_TO_CLASSES.items() if k in ("blue_chip_steady", "contrarian_dip_buyer")],
)
def test_stateful_strategies_persist_state_across_decide_calls(key, cls, bars_30_symbols, tmp_path, monkeypatch):
    """Blue-Chip Steady's picks/entry-prices and Contrarian Dip Buyer's
    tranche levels must round-trip through disk between separate process
    invocations, the same mechanism pattern_recognition.py relies on."""
    from dashboard.backend.domain.leaderboard.strategies import _signal_engine

    monkeypatch.setattr(_signal_engine, "LIVE_STATE_DIR", tmp_path)
    history = _signal_engine.daily_history(bars_30_symbols)

    strat_a = cls({})
    weights_a = strat_a.decide(history)

    state_files = list(tmp_path.glob("*.json"))
    assert state_files, "decide() did not persist any state"

    # A brand-new instance (simulating a fresh process) should read the same
    # persisted state back rather than starting cold.
    strat_b = cls({})
    weights_b = strat_b.decide(history)
    assert isinstance(weights_b, dict)
    # Whether this particular synthetic price path actually triggers a
    # position depends on random chance (a dip deep enough for Contrarian Dip
    # Buyer, say); what matters here is that state round-tripped without
    # crashing and produced a well-typed result both times.
    assert isinstance(weights_a, dict)
