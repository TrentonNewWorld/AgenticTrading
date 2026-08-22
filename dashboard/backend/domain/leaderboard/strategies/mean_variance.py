"""Mean-variance (Markowitz) optimal portfolio over a universe (default DJIA 30).

Estimates the long-only maximum-Sharpe portfolio from the **prior-month
reference window** (see ``reference_start_date`` in leaderboard config),
then allocates at the contest open and holds through the contest window.

Weights are out-of-sample: covariance/returns use only reference-period bars,
not the contest month being evaluated.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from dashboard.backend.infrastructure.llm.validator import DJIA_30

from .base import BaselineStrategy
from ._common import (
    build_price_cache,
    equity_curve_from_positions,
    market_timestamps,
    reference_start_date,
    subset_bars,
    timestamps_in_contest,
    timestamps_in_reference,
)
from ._signal_engine import DailyHistory, load_strategy_state, save_strategy_state

_REFIT_LOOKBACK_DAYS = 21


class MeanVarianceStrategy(BaselineStrategy):
    key = "mean_variance"

    def required_symbols(self) -> List[str]:
        symbols = self.config.get("symbols")
        return list(symbols) if symbols else list(DJIA_30)

    def _optimal_weights(self, returns: np.ndarray) -> np.ndarray:
        """Long-only max-Sharpe (tangency) weights with rf=0.

        Uses the pseudo-inverse for numerical stability, clips short positions,
        and renormalizes. Falls back to equal weight when degenerate.
        """
        n = returns.shape[1]
        mu = returns.mean(axis=0)
        cov = np.cov(returns, rowvar=False)
        if cov.ndim == 0:
            cov = cov.reshape(1, 1)

        try:
            raw = np.linalg.pinv(cov) @ mu
        except np.linalg.LinAlgError:
            raw = np.ones(n)

        weights = np.clip(raw, 0.0, None)
        total = weights.sum()
        if not np.isfinite(total) or total <= 0:
            return np.ones(n) / n
        return weights / total

    def run(
        self,
        bars_by_symbol: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
        initial_capital: float,
    ) -> List[Dict[str, Any]]:
        symbols = self.required_symbols()
        bars_subset = subset_bars(bars_by_symbol, symbols)
        if not bars_subset:
            return []

        all_timestamps = market_timestamps(bars_subset)
        if not all_timestamps:
            return []

        ref_start = reference_start_date(start_date, self.config)
        ref_timestamps = timestamps_in_reference(all_timestamps, ref_start, start_date)
        contest_timestamps = timestamps_in_contest(all_timestamps, start_date, end_date)
        if len(ref_timestamps) < 3 or not contest_timestamps:
            return []

        price_cache = build_price_cache(bars_subset, all_timestamps)
        active_symbols = sorted(price_cache.keys())
        if not active_symbols:
            return []

        ref_matrix = np.array(
            [[price_cache[sym][ts] for sym in active_symbols] for ts in ref_timestamps],
            dtype=float,
        )
        if ref_matrix.shape[0] < 3:
            return []

        ref_returns = ref_matrix[1:] / ref_matrix[:-1] - 1.0
        weights = self._optimal_weights(ref_returns)

        first_contest_ts = contest_timestamps[0]
        first_prices = np.array(
            [price_cache[sym][first_contest_ts] for sym in active_symbols],
            dtype=float,
        )
        positions: Dict[str, int] = {}
        cash = float(initial_capital)
        for idx, symbol in enumerate(active_symbols):
            price = first_prices[idx]
            if price <= 0:
                continue
            allocation = initial_capital * weights[idx]
            shares = int(allocation / price)
            if shares > 0:
                positions[symbol] = shares
                cash -= shares * price

        if not positions:
            return []

        self._num_positions = len(positions)
        print(
            f"\n   📋 Mean-variance baseline: {len(positions)} positions, "
            f"top weight {weights.max():.1%} "
            f"(weights from {ref_start} → {start_date}, contest {start_date} → {end_date})"
        )
        contest_price_cache = {
            sym: {ts: prices[ts] for ts in contest_timestamps if ts in prices}
            for sym, prices in price_cache.items()
        }
        return equity_curve_from_positions(
            positions, cash, contest_price_cache, contest_timestamps
        )

    def num_trades(self) -> int:
        return getattr(self, "_num_positions", 0)

    def decide(self, history: DailyHistory) -> Optional[Dict[str, float]]:
        """Live/paper-trading entrypoint. Re-fits monthly (a ~21-trading-day
        reference window, same out-of-sample principle as ``run()``'s
        prior-month contest reference), persisting the fitted weights and the
        month they were fit in. Calls within the same month return ``None``
        ("make no trades this cycle") rather than refitting on noise every
        single day."""
        state = load_strategy_state(self.key)
        cur_month = str(history.close.index[-1].to_period("M"))
        if state.get("month") == cur_month and state.get("weights"):
            return None

        close = history.close.dropna(axis=1, how="any")
        window = min(_REFIT_LOOKBACK_DAYS, len(close))
        if window < 3 or close.shape[1] == 0:
            return None
        ref_returns = close.iloc[-window:].pct_change().dropna(how="all").values
        if ref_returns.shape[0] < 3:
            return None

        active_symbols = list(close.columns)
        weights = self._optimal_weights(ref_returns)
        target = {sym: float(w) for sym, w in zip(active_symbols, weights) if w > 0}
        if not target:
            return None
        save_strategy_state(self.key, {"month": cur_month, "weights": target})
        return target
