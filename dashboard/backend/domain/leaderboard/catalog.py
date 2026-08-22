"""Strategy Catalog: every registered baseline strategy that cleared a 3%+
trailing-year return in the "Strategy Lab" backtest report, presented as one
card each (name, description, own equity-curve chart, metrics) and made
selectable for paper and live trading through the existing
``alpaca_paper_service``/``alpaca_live_service`` run-by-strategy-key paths.

Computing all 28 strategies' full-year equity curves against real Alpaca
data on every page load would be slow and would burn API quota on every
visitor, so results are cached to a small JSON file
(``dashboard/storage/data/strategy_catalog_cache.json``) and only
recomputed when the cache is empty, stale (see ``CACHE_TTL_HOURS``), or a
caller explicitly asks for ``force_refresh``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from dashboard.backend.domain.leaderboard.strategies import get_strategy
from dashboard.backend.infrastructure.llm.validator import DJIA_30
from dashboard.backend.paths import REPO_ROOT

CACHE_PATH = REPO_ROOT / "dashboard" / "storage" / "data" / "strategy_catalog_cache.json"
CACHE_TTL_HOURS = 24
INITIAL_CAPITAL = 1000.0


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    name: str
    source: str
    description: str


#: All 28 strategies that cleared 3%+ in the full-year "Strategy Lab" retest
#: (2025-08-21 -> 2026-08-21; MultiMa and Pairs Trading, the only two that
#: lost money, are not included). Every one of these has a `decide()` method
#: and is therefore selectable for paper/live trading, not just display.
CATALOG_ENTRIES: List[CatalogEntry] = [
    CatalogEntry("momentum_effect", "Momentum Effect", "QuantConnect",
        "Ranks all 30 Dow stocks by trailing 12-month total return and holds the top 10 equally weighted, rebalanced monthly."),
    CatalogEntry("capm_alpha_ranking", "CAPM Alpha Ranking", "QuantConnect",
        "Runs a rolling 60-day CAPM regression of each stock's returns against the equal-weight Dow-30 market return, ranks by alpha, and holds the top 2 alpha generators equally weighted, rebalanced monthly."),
    CatalogEntry("contrarian_dip_buyer", "Contrarian Dip Buyer", "Marketplace",
        "Buys a stock in increasing tranches (up to 3) the further it falls below its 20-day high (10%/15%/20% thresholds), weighted by how deep the dip is; exits once the stock recovers to within 2% of that high."),
    CatalogEntry("tradingagents_composite", "TradingAgents (technical composite)", "TradingAgents",
        "Composites RSI, MACD histogram, 50/200-day SMA position, and Bollinger %B into one score; holds the top 8 Dow names by that score, rebalanced every 5 days. A proxy for TradingAgents' technical analyst -- the framework's own bull/bear/risk debate stages are LLM judgment calls with no numeric formula."),
    CatalogEntry("ai_hedge_fund", "AI Hedge Fund", "Marketplace",
        "Composite score of 12-month momentum (skipping the most recent month), low realized volatility, and 5-day return; holds the top 8 scorers, rebalanced monthly. Approximates virattt's ai-hedge-fund analyst panel with no fundamentals/news feed."),
    CatalogEntry("almgren_chriss_twap", "AlmgrenChriss / TWAP", "freqtrade",
        "Buys any stock whose RSI drops below 45 and exits once RSI recovers above 50. Both freqtrade source strategies use this identical signal on daily bars."),
    CatalogEntry("universal_macd", "UniversalMACD (zero-cross)", "freqtrade",
        "Buys when a normalized MACD ratio (12-day EMA / 26-day EMA, minus 1) crosses from negative to positive, sells on the reverse cross."),
    CatalogEntry("volatility_guard", "Volatility Guard", "Marketplace",
        "Holds the top 8 momentum stocks at full weight while 10-day price volatility stays under 1.2x its 60-day average; cuts to the top 4 above that, and the top 2 above 1.6x."),
    CatalogEntry("supertrend_triple", "Supertrend x3", "freqtrade",
        "Combines three ATR-based Supertrend indicators at different sensitivities (7/3, 10/3, 14/4 period/multiplier); only buys when all three agree the trend is up, exits when all three flip down."),
    CatalogEntry("short_term_reversal", "Short-Term Reversal", "QuantConnect",
        "Ranks stocks by their prior month's return and holds the 10 worst performers equally weighted, rebalanced monthly, betting on short-term mean reversion. The original's short leg (best performers) is dropped since this engine is long-only."),
    CatalogEntry("mean_variance", "Mean-Variance", "Leaderboard baseline",
        "Estimates the long-only, maximum-Sharpe-ratio portfolio each month from the prior 21 trading days of returns (Markowitz optimization via matrix pseudo-inverse), holds those weights for the following month."),
    CatalogEntry("pattern_recognition", "Pattern Recognition", "freqtrade",
        "Buys when a stock forms a 'high wave' candle (small body, long shadows both directions, signaling indecision) at a fresh 10-day low; holds for a fixed 10 trading days."),
    CatalogEntry("balanced_starter", "Balanced Starter", "Marketplace",
        "Equal-weights the top 8 stocks by 20-day momentum; overweights a name trading 3%+ below its 20-day average, trims a name up 15%+ in a month, capped at 20% per position."),
    CatalogEntry("market_index_spy", "Market Index: S&P 500 (SPY)", "Leaderboard baseline",
        "Simply holds the SPY ETF, tracking the S&P 500 -- a passive market comparison."),
    CatalogEntry("equal_weight_index", "Equal-Weight Index", "Leaderboard baseline",
        "Rebalances back to equal weight across all 30 Dow stocks every single trading day."),
    CatalogEntry("market_index_djia", "Market Index: DJIA (DIA)", "Leaderboard baseline",
        "Simply holds the DIA ETF, tracking the Dow Jones Industrial Average -- the passive benchmark every Dow-30 strategy here is implicitly trying to beat."),
    CatalogEntry("even_split_dow", "Even-Split Dow", "Marketplace",
        "Equal-weights all 30 Dow stocks, rebalanced monthly back to even shares."),
    CatalogEntry("equal_weight_buyhold", "Equal-Weight Buy & Hold", "Leaderboard baseline",
        "Buys equal dollar amounts of all 30 Dow stocks on day one and holds without rebalancing, letting weights drift with each stock's own price movement."),
    CatalogEntry("momentum_scout", "Momentum Scout", "Marketplace",
        "Ranks by 10-day price momentum confirmed by above-average volume; holds the top 6 names with positive momentum, overweights a name pulling back 1-6% from its recent high within an intact uptrend."),
    CatalogEntry("three_step_analyst", "Three-Step Analyst", "Marketplace",
        "Only holds stocks where the 20-day average price is above the 50-day average (an uptrend filter), sized by 20-day momentum, across up to 10 names."),
    CatalogEntry("blue_chip_steady", "Blue-Chip Steady", "Marketplace",
        "Picks the 8 Dow stocks with the best trailing ~1-year return once, holds them equally weighted, and sells a name entirely if it falls 25% from its entry price."),
    CatalogEntry("volatility_effect", "Volatility Effect", "QuantConnect",
        "Computes each stock's trailing 252-day return volatility and holds the 5 lowest-volatility names equally weighted, rebalanced monthly -- a defensive, low-volatility-anomaly strategy."),
    CatalogEntry("hlhb", "hlhb", "freqtrade",
        "Buys when RSI crosses above 50, the 5-day EMA rises above the 10-day EMA, and ADX confirms a real trend (>25); sells on the mirrored bearish combination."),
    CatalogEntry("overnight_anomaly", "Overnight Anomaly (SPY)", "QuantConnect",
        "Buys SPY at every day's close and sells at the next day's open, capturing only the overnight return."),
    CatalogEntry("bandtastic", "Bandtastic", "freqtrade",
        "Buys a stock when its price falls below its 20-day Bollinger lower band while RSI stays under 52 and its 10-day EMA is above its 50-day EMA; sells on the mirror-image condition at the upper band."),
    CatalogEntry("turn_of_month", "Turn of the Month (SPY)", "QuantConnect",
        "Buys SPY at the open on the last trading day of each month and holds for 3 trading days before selling."),
    CatalogEntry("trendrider", "TrendRider (simplified)", "freqtrade",
        "Enters on a golden cross, an RSI bounce off oversold above the 200-day average, or a MACD histogram turning positive; exits on RSI overheating, a bearish EMA cross, or price falling below the 200-day average."),
    CatalogEntry("sector_rotator", "Sector Rotator", "Marketplace",
        "Groups the Dow 30 into 8 rough sectors, holds the top 3 names from whichever sector had the best trailing-month average return, checked monthly."),
]

#: Registry keys actually used by `.run()` (market_index needs a `symbols`
#: override to distinguish DJIA/SPY -- both share the same `market_index`
#: strategy class).
_REGISTRY_KEY_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "market_index_spy": {"strategy": "market_index", "symbols": ["SPY"]},
    "market_index_djia": {"strategy": "market_index", "symbols": ["DIA"]},
}


def _config_for(entry: CatalogEntry) -> Dict[str, Any]:
    override = _REGISTRY_KEY_OVERRIDES.get(entry.key)
    if override:
        return {"id": entry.key, "name": entry.name, **override}
    return {"id": entry.key, "name": entry.name, "strategy": entry.key}


#: Overnight Anomaly and Turn of the Month are single-instrument (SPY)
#: intraday round-trip strategies -- buy at one session boundary, sell at
#: another within the same or next session. That doesn't reduce to a single
#: "target weight for today" call the way every other strategy's decide()
#: does, so neither defines one, and both are display-only here (a "Run in
#: Paper"/"Run in Live" button for them would just raise
#: `strategy has no live-trading decide() method`).
_NOT_SELECTABLE_FOR_LIVE_TRADING = {"overnight_anomaly", "turn_of_month"}


def is_selectable_for_live_trading(key: str) -> bool:
    return key not in _NOT_SELECTABLE_FOR_LIVE_TRADING


def _fetch_daily_bars(symbols: List[str], end: datetime, lookback_days: int) -> Dict[str, pd.DataFrame]:
    """Real Alpaca daily bars for a full year+ window (not the live-trading
    `fetch_daily_history` helper in `_alpaca_strategy_shared.py`, which
    returns an already-merged `DailyHistory` -- `.run()` wants one raw
    DataFrame per symbol instead)."""
    import os

    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        return {}

    client = StockHistoricalDataClient(api_key, secret_key)
    start = end - timedelta(days=lookback_days)
    request = StockBarsRequest(
        symbol_or_symbols=symbols, timeframe=TimeFrame.Day, start=start, end=end,
        feed=DataFeed.SIP, adjustment="all",
    )
    bars = client.get_stock_bars(request)
    df = bars.df
    if df.empty:
        return {}

    result: Dict[str, pd.DataFrame] = {}
    for sym, sub in df.groupby(level=0):
        sub = sub.droplevel(0)
        idx = pd.to_datetime(sub.index)
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        # Re-tag each daily bar to that trading day's 16:00 ET close.
        # `_common.py`'s `market_timestamps`/`filter_market_hours` (built for
        # intraday hourly bars, which the leaderboard/contest engine always
        # feeds `.run()`) drops anything outside 9:30-16:00 ET -- a daily
        # bar's raw midnight timestamp would otherwise be filtered out
        # entirely, silently emptying every curve.
        et_dates = idx.tz_convert("US/Eastern").normalize()
        close_et = et_dates + pd.Timedelta(hours=16)
        sub.index = close_et.tz_convert("UTC")
        result[sym] = sub[["open", "high", "low", "close", "volume"]]
    return result


def _metrics(curve: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not curve:
        return {"final": INITIAL_CAPITAL, "return_pct": 0.0, "max_drawdown_pct": 0.0}
    equity = np.array([row["equity"] for row in curve], dtype=float)
    final = float(equity[-1])
    ret_pct = (final / INITIAL_CAPITAL - 1) * 100
    peak = np.maximum.accumulate(equity)
    dd = float(((equity - peak) / peak).min() * 100) if len(equity) else 0.0
    return {"final": round(final, 2), "return_pct": round(ret_pct, 2), "max_drawdown_pct": round(dd, 2)}


def _compute_all(force_refresh: bool = False) -> Dict[str, Any]:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    symbols = sorted(set(DJIA_30) | {"SPY", "DIA"})
    bars_by_symbol = _fetch_daily_bars(symbols, end, lookback_days=400)
    if not bars_by_symbol:
        return {"computed_at": None, "entries": []}

    all_dates = sorted({d.date() for df in bars_by_symbol.values() for d in df.index})
    if len(all_dates) < 30:
        return {"computed_at": None, "entries": []}
    test_dates = all_dates[-252:] if len(all_dates) > 252 else all_dates
    start_date = test_dates[0].isoformat()
    end_date = test_dates[-1].isoformat()

    entries_out = []
    for entry in CATALOG_ENTRIES:
        try:
            strat = get_strategy(_config_for(entry))
            required = strat.required_symbols()
            subset = {s: bars_by_symbol[s] for s in required if s in bars_by_symbol}
            curve = strat.run(subset, start_date, end_date, INITIAL_CAPITAL) if subset else []
            metrics = _metrics(curve)
            metrics["n_trades"] = strat.num_trades()
            if curve and abs(metrics["final"] - INITIAL_CAPITAL) < 0.01:
                # A handful of production strategies buy in whole-share lots
                # (no $10-lot quantization); at $1,000 across 30 Dow names
                # that can be less than one share of a higher-priced stock,
                # leaving the whole run in cash. That is a real property of
                # this strategy's implementation at this capital, not a
                # market outcome worth presenting as "returned exactly 0%".
                metrics["note"] = (
                    "This strategy buys whole shares rather than fractional "
                    "$10 lots; $1,000 split across its universe wasn't enough "
                    "to buy a full share of most names, so it stayed in cash."
                )
            # Sample down to ~1 point every 3 trading days for a lighter payload.
            sampled = curve[::3] if len(curve) > 120 else curve
            if curve and sampled[-1] is not curve[-1]:
                sampled = sampled + [curve[-1]]
        except Exception as exc:  # a single strategy's data hiccup must not blank the whole catalog
            metrics = {"final": INITIAL_CAPITAL, "return_pct": 0.0, "max_drawdown_pct": 0.0, "n_trades": 0}
            sampled = []
            metrics["error"] = str(exc)[:200]

        entries_out.append({
            "key": entry.key,
            "name": entry.name,
            "source": entry.source,
            "description": entry.description,
            "metrics": metrics,
            "equity_curve": [{"t": row["timestamp"], "equity": row["equity"]} for row in sampled],
            "selectable": is_selectable_for_live_trading(entry.key),
        })

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start_date": start_date, "end_date": end_date},
        "initial_capital": INITIAL_CAPITAL,
        "entries": entries_out,
    }


def _load_cache() -> Optional[Dict[str, Any]]:
    if not CACHE_PATH.exists():
        return None
    try:
        with CACHE_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(payload: Dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f)


def _is_stale(payload: Dict[str, Any]) -> bool:
    computed_at = payload.get("computed_at")
    if not computed_at:
        return True
    try:
        ts = datetime.fromisoformat(computed_at)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - ts > timedelta(hours=CACHE_TTL_HOURS)


def get_strategy_catalog(force_refresh: bool = False) -> Dict[str, Any]:
    """Entry point for the API router. Returns the cached catalog unless it
    is missing, stale, or the caller forces a refresh -- see the module
    docstring for why this isn't computed fresh on every request."""
    if not force_refresh:
        cached = _load_cache()
        if cached and not _is_stale(cached):
            return cached

    payload = _compute_all(force_refresh=force_refresh)
    if payload.get("entries"):
        _save_cache(payload)
        return payload

    # Computation failed (no credentials, no data) -- serve a stale cache
    # rather than an empty page if one exists.
    cached = _load_cache()
    return cached or payload
