"""Strategy Catalog: every non-Model entry in ``dashboard/config/leaderboard
.json`` that supports live/paper trading, presented as one card each (name,
description, own equity-curve chart, metrics) and made selectable through the
existing ``alpaca_paper_service``/``alpaca_live_service``
run-by-strategy-key paths.

The roster is deliberately NOT hardcoded here -- it is read from
leaderboard.json on every call (``_catalog_roster``), the same file the
Competition Leaderboard's chart reads. Adding a strategy there puts it on
both the chart and this catalog in one place; ``remove_strategy`` (called by
the catalog's Remove button) edits that same file, so the two surfaces can
never drift out of sync the way two independently-maintained lists would.

Computing every strategy's full-year equity curve against real Alpaca data
on every page load would be slow and would burn API quota on every visitor,
so results are cached to a small JSON file
(``dashboard/storage/data/strategy_catalog_cache.json``) and only
recomputed when the cache is empty, stale (see ``CACHE_TTL_HOURS``), or a
caller explicitly asks for ``force_refresh``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from dashboard.backend.domain.leaderboard.strategies import get_strategy
from dashboard.backend.domain.leaderboard.strategies.llm_agent import MAX_STRATEGY_PROMPT_CHARS
from dashboard.backend.domain.leaderboard.strategy_overrides import apply_overrides
from dashboard.backend.infrastructure.llm.validator import DJIA_30
from dashboard.backend.paths import CONFIG_DIR, REPO_ROOT

CACHE_PATH = REPO_ROOT / "dashboard" / "storage" / "data" / "strategy_catalog_cache.json"
LEADERBOARD_CONFIG_PATH = CONFIG_DIR / "leaderboard.json"
CACHE_TTL_HOURS = 24
INITIAL_CAPITAL = 1000.0


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    name: str
    source: str
    description: str


#: Rich descriptions for the strategies already known to this catalog (the 23
#: that cleared the full-year "Strategy Lab" retest, then a further $10,100
#: filter on the Competition Leaderboard's own numbers -- see the git history
#: for that accounting). Keyed by leaderboard.json's `id`, NOT hardcoded as
#: the roster itself: the roster is now derived from leaderboard.json below,
#: so removing an entry there removes it here too, and the Competition
#: Leaderboard's chart and this catalog can never drift out of sync. A
#: strategy added to leaderboard.json with no entry here still shows up, just
#: with a generic placeholder description (see _description_for).
_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "momentum_effect": {"source": "QuantConnect",
        "description": "Ranks all 30 Dow stocks by trailing 12-month total return and holds the top 10 equally weighted, rebalanced monthly."},
    "capm_alpha_ranking": {"source": "QuantConnect",
        "description": "Runs a rolling 60-day CAPM regression of each stock's returns against the equal-weight Dow-30 market return, ranks by alpha, and holds the top 2 alpha generators equally weighted, rebalanced monthly."},
    "contrarian_dip_buyer": {"source": "Marketplace",
        "description": "Buys a stock in increasing tranches (up to 3) the further it falls below its 20-day high (10%/15%/20% thresholds), weighted by how deep the dip is; exits once the stock recovers to within 2% of that high."},
    "tradingagents_composite": {"source": "TradingAgents",
        "description": "Composites RSI, MACD histogram, 50/200-day SMA position, and Bollinger %B into one score; holds the top 8 Dow names by that score, rebalanced every 5 days. A proxy for TradingAgents' technical analyst -- the framework's own bull/bear/risk debate stages are LLM judgment calls with no numeric formula."},
    "ai_hedge_fund": {"source": "Marketplace",
        "description": "Composite score of 12-month momentum (skipping the most recent month), low realized volatility, and 5-day return; holds the top 8 scorers, rebalanced monthly. Approximates virattt's ai-hedge-fund analyst panel with no fundamentals/news feed."},
    "almgren_chriss_twap": {"source": "freqtrade",
        "description": "Buys any stock whose RSI drops below 45 and exits once RSI recovers above 50. Both freqtrade source strategies use this identical signal on daily bars."},
    "universal_macd": {"source": "freqtrade",
        "description": "Buys when a normalized MACD ratio (12-day EMA / 26-day EMA, minus 1) crosses from negative to positive, sells on the reverse cross."},
    "volatility_guard": {"source": "Marketplace",
        "description": "Holds the top 8 momentum stocks at full weight while 10-day price volatility stays under 1.2x its 60-day average; cuts to the top 4 above that, and the top 2 above 1.6x."},
    "supertrend_triple": {"source": "freqtrade",
        "description": "Combines three ATR-based Supertrend indicators at different sensitivities (7/3, 10/3, 14/4 period/multiplier); only buys when all three agree the trend is up, exits when all three flip down."},
    "short_term_reversal": {"source": "QuantConnect",
        "description": "Ranks stocks by their prior month's return and holds the 10 worst performers equally weighted, rebalanced monthly, betting on short-term mean reversion. The original's short leg (best performers) is dropped since this engine is long-only."},
    "mean_variance_djia": {"source": "Leaderboard baseline",
        "description": "Estimates the long-only, maximum-Sharpe-ratio portfolio each month from the prior 21 trading days of returns (Markowitz optimization via matrix pseudo-inverse), holds those weights for the following month."},
    "pattern_recognition": {"source": "freqtrade",
        "description": "Buys when a stock forms a 'high wave' candle (small body, long shadows both directions, signaling indecision) at a fresh 10-day low; holds for a fixed 10 trading days."},
    "balanced_starter": {"source": "Marketplace",
        "description": "Equal-weights the top 8 stocks by 20-day momentum; overweights a name trading 3%+ below its 20-day average, trims a name up 15%+ in a month, capped at 20% per position."},
    "spy_index": {"source": "Leaderboard baseline",
        "description": "Simply holds the SPY ETF, tracking the S&P 500 -- a passive market comparison."},
    "equal_weight_djia": {"source": "Leaderboard baseline",
        "description": "Rebalances back to equal weight across all 30 Dow stocks every single trading day."},
    "djia_index": {"source": "Leaderboard baseline",
        "description": "Simply holds the DIA ETF, tracking the Dow Jones Industrial Average -- the passive benchmark every Dow-30 strategy here is implicitly trying to beat."},
    "even_split_dow": {"source": "Marketplace",
        "description": "Equal-weights all 30 Dow stocks, rebalanced monthly back to even shares."},
    "momentum_scout": {"source": "Marketplace",
        "description": "Ranks by 10-day price momentum confirmed by above-average volume; holds the top 6 names with positive momentum, overweights a name pulling back 1-6% from its recent high within an intact uptrend."},
    "three_step_analyst": {"source": "Marketplace",
        "description": "Only holds stocks where the 20-day average price is above the 50-day average (an uptrend filter), sized by 20-day momentum, across up to 10 names."},
    "blue_chip_steady": {"source": "Marketplace",
        "description": "Picks the 8 Dow stocks with the best trailing ~1-year return once, holds them equally weighted, and sells a name entirely if it falls 25% from its entry price."},
    "volatility_effect": {"source": "QuantConnect",
        "description": "Computes each stock's trailing 252-day return volatility and holds the 5 lowest-volatility names equally weighted, rebalanced monthly -- a defensive, low-volatility-anomaly strategy."},
    "bandtastic": {"source": "freqtrade",
        "description": "Buys a stock when its price falls below its 20-day Bollinger lower band while RSI stays under 52 and its 10-day EMA is above its 50-day EMA; sells on the mirror-image condition at the upper band."},
    "trendrider": {"source": "freqtrade",
        "description": "Enters on a golden cross, an RSI bounce off oversold above the 200-day average, or a MACD histogram turning positive; exits on RSI overheating, a bearish EMA cross, or price falling below the 200-day average."},
}


def _description_for(strategy_id: str, model_name: str, strat: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    # An inline description on the leaderboard.json entry itself (written by
    # convert_agent_to_strategy()) wins over the hand-maintained _DESCRIPTIONS
    # dict -- a converted agent's description is generated at conversion
    # time, not hardcoded into this module.
    if strat and strat.get("description"):
        return {"source": strat.get("source") or "Baseline Strategy", "description": strat["description"]}
    return _DESCRIPTIONS.get(strategy_id) or {
        "source": "Baseline Strategy",
        "description": f"{model_name} -- no description written yet.",
    }


def load_leaderboard_config() -> Dict[str, Any]:
    with LEADERBOARD_CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _save_leaderboard_config(config: Dict[str, Any]) -> None:
    with LEADERBOARD_CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def remove_strategy(key: str) -> bool:
    """Remove one strategy from leaderboard.json -- the single roster both
    this catalog and the Competition Leaderboard's chart read from, so a
    removal here disappears from both at once. Returns False if the key
    wasn't an AI model (never removable from here) or wasn't found."""
    config = load_leaderboard_config()
    strategies = config.get("strategies", [])
    kept = [s for s in strategies if s.get("id") != key]
    removed = next((s for s in strategies if s.get("id") == key), None)
    if removed is None:
        return False
    if removed.get("label") == "Model":
        raise ValueError("AI model entries cannot be removed from the Strategy Catalog")
    config["strategies"] = kept
    _save_leaderboard_config(config)
    # Evict from the served cache too, not just the roster file. The page
    # reads the cached payload for up to CACHE_TTL_HOURS; without this a
    # successful removal stayed on screen for up to a day and the Remove
    # button looked broken (observed live 2026-08-29: DELETE returned 200,
    # leaderboard.json was updated, and the entry kept rendering from the
    # stale cache). Editing the cache in place -- rather than recomputing --
    # keeps Remove instant.
    cached = _load_cache()
    if cached and isinstance(cached.get("entries"), list):
        cached["entries"] = [e for e in cached["entries"] if e.get("key") != key]
        _save_cache(cached)
    return True


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "agent"


def _unique_strategy_id(base_slug: str, config: Dict[str, Any]) -> str:
    existing = {s.get("id") for s in config.get("strategies", [])}
    candidate = base_slug
    n = 2
    while candidate in existing:
        candidate = f"{base_slug}_{n}"
        n += 1
    return candidate


def convert_agent_to_strategy(
    *, name: str, model_id: Optional[str], strategy_prompt: str, symbols: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Write a new Strategy Catalog entry that trades exactly like the given
    agent's pipeline instruction, via the ``llm_agent`` strategy (its
    ``strategy_prompt`` config already replaces the mode's default prompt
    body wholesale -- see llm_agent.py). Called from the agent-conversion
    route (api/routers/agents.py) -- the source agent is only deleted by that
    caller once this returns successfully, so a failure here never destroys
    an agent with nothing to show for it.

    ``auto_compute: false`` matches the convention the 7 competition-model
    entries already use: the Competition Leaderboard's own recompute path
    (domain/leaderboard/service.py::_auto_compute) skips any entry with this
    flag, since an LLM-backed curve is expensive/billable and must not be
    silently regenerated on every leaderboard poll. This catalog's own
    _compute_all() does not check the flag -- a converted agent's backtest
    curve fills in on this page's own (24h-cached) refresh cycle."""
    prompt = (strategy_prompt or "").strip()
    if not prompt:
        raise ValueError("this agent has no trading instruction to convert")
    if len(prompt) > MAX_STRATEGY_PROMPT_CHARS:
        raise ValueError(
            f"strategy_prompt is {len(prompt)} characters, over the {MAX_STRATEGY_PROMPT_CHARS} limit"
        )

    config = load_leaderboard_config()
    strategy_id = _unique_strategy_id(_slugify(name), config)
    truncated_prompt = prompt if len(prompt) <= 300 else prompt[:297] + "..."
    entry: Dict[str, Any] = {
        "id": strategy_id,
        "name": name,
        "label": "Baseline Strategy",
        "model": name,
        "strategy": "llm_agent",
        "model_id": model_id,
        "mode": "safe_trading",
        "symbols": list(symbols) if symbols else [],
        "auto_compute": False,
        "source": "Converted Agent",
        "description": (
            f'Converted from the dashboard agent "{name}". Trades using this '
            f'custom instruction: "{truncated_prompt}"'
        ),
    }
    config.setdefault("strategies", []).append(entry)
    _save_leaderboard_config(config)
    append_placeholder_to_cache(
        key=strategy_id, name=name, source=entry["source"], description=entry["description"],
    )
    return entry


#: leaderboard.json configures market_index with Yahoo index tickers
#: (^GSPC/^DJI) so the Competition Leaderboard's backtest tracks the actual
#: index level, not an ETF's tracking-error drift. But market_index.decide()
#: returns None for any "^"-prefixed symbol -- there is nothing to buy -- so
#: using that same config here would show these two as selectable and then
#: silently do nothing on every Run click. Substitute the tradeable ETF
#: proxy (also used for the full-year backtest curve on this page, for the
#: same reason: it should reflect what Run-in-Paper/Run-in-Live would
#: actually do) only here, never in leaderboard.json itself.
_TRADEABLE_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "spy_index": {"strategy": "market_index", "symbols": ["SPY"]},
    "djia_index": {"strategy": "market_index", "symbols": ["DIA"]},
}


def _catalog_roster() -> List[CatalogEntry]:
    """Every non-Model entry in leaderboard.json that actually supports
    live/paper trading (has decide()) -- Overnight Anomaly/Turn of the Month
    style single-instrument intraday strategies, if any are ever re-added,
    would resolve but lack decide() and are skipped rather than shown with
    dead Run buttons."""
    config = load_leaderboard_config()
    entries: List[CatalogEntry] = []
    for strat in config.get("strategies", []):
        if strat.get("label") == "Model":
            continue
        key = strat.get("id")
        model_name = strat.get("model") or key
        resolve_config = _TRADEABLE_OVERRIDES.get(key) or {
            "strategy": strat.get("strategy"), "symbols": strat.get("symbols"),
        }
        try:
            resolved = get_strategy(resolve_config)
        except Exception:
            continue
        if not hasattr(resolved, "decide"):
            continue
        meta = _description_for(key, model_name, strat)
        entries.append(CatalogEntry(key, model_name, meta["source"], meta["description"]))
    return entries


def _config_for(entry: CatalogEntry) -> Dict[str, Any]:
    tradeable_override = _TRADEABLE_OVERRIDES.get(entry.key)
    if tradeable_override:
        base = {"id": entry.key, "name": entry.name, **tradeable_override}
    else:
        config = load_leaderboard_config()
        strat = next((s for s in config.get("strategies", []) if s.get("id") == entry.key), None)
        if strat is None:
            base = {"id": entry.key, "name": entry.name, "strategy": entry.key}
        else:
            # Pass the whole leaderboard.json entry through (minus the
            # display-only fields) rather than hand-picking strategy/symbols --
            # a strategy with its own extra config (llm_agent's
            # model_id/mode/strategy_prompt) needs those to survive into
            # get_strategy(); BaselineStrategy subclasses that don't read a
            # given key just ignore it via .get().
            base = {k: v for k, v in strat.items() if k not in ("label", "source", "description")}
            base.setdefault("id", entry.key)
            base.setdefault("name", entry.name)
    return apply_overrides(entry.key, base)


def resolve_run_config(key: str, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """The ``get_strategy()`` config for a Strategy Catalog key -- the single
    resolution path ``execution/alpaca_paper_service.py`` and
    ``alpaca_live_service.py`` must go through for Run in Paper/Run in Live,
    instead of assuming a catalog key IS the registry ``strategy`` type
    directly.

    That assumption is wrong for any entry whose catalog id differs from its
    registry key -- ``spy_index``/``djia_index`` (both -> ``market_index``,
    distinguished only by the ``_TRADEABLE_OVERRIDES`` symbols override
    above), ``mean_variance_djia`` (-> ``mean_variance``), and
    ``equal_weight_djia`` (-> ``equal_weight_index``) as of this writing.
    Calling ``get_strategy({"strategy": key, ...})`` with the raw catalog key
    for one of those 500s with "Unknown baseline strategy" (caught live: a
    real user hit exactly this trying to run equal_weight_djia for real
    money) -- and worse, even for entries where the two names DO happen to
    match, skipping this path means Run in Paper/Run in Live silently miss
    ``_TRADEABLE_OVERRIDES`` and any non-symbols config a leaderboard.json
    entry carries (llm_agent's model_id/mode/strategy_prompt, etc.), the
    exact class of bug ``_config_for``'s own comment above already warns
    about for the backtest-preview path this mirrors."""
    entry = next((e for e in _catalog_roster() if e.key == key), None)
    if entry is None:
        raise ValueError(f"no strategy '{key}' in the catalog")
    config = _config_for(entry)
    if symbols:
        config = dict(config)
        config["symbols"] = symbols
    return config


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
    for entry in _catalog_roster():
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
        })

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start_date": start_date, "end_date": end_date},
        "initial_capital": INITIAL_CAPITAL,
        "entries": entries_out,
    }


def generate_report(key: str) -> Dict[str, Any]:
    """Month-by-month + overall P&L for one catalog strategy, always at the
    same $1,000 starting wallet every card uses -- the "Reports" button's data
    source. Recomputes the full (unsampled) daily curve fresh rather than
    reusing `_compute_all`'s cached, display-sampled one (that one is
    thinned to ~1 point per 3 trading days for a lighter payload, which is
    fine for a chart but would blur month-boundary values here)."""
    entry = next((e for e in _catalog_roster() if e.key == key), None)
    if entry is None:
        raise ValueError(f"no strategy '{key}' in the catalog")

    end = datetime.now(timezone.utc) - timedelta(days=1)
    symbols = sorted(set(DJIA_30) | {"SPY", "DIA"})
    bars_by_symbol = _fetch_daily_bars(symbols, end, lookback_days=400)
    if not bars_by_symbol:
        raise RuntimeError("no market data available (check ALPACA_API_KEY/ALPACA_SECRET_KEY)")

    all_dates = sorted({d.date() for df in bars_by_symbol.values() for d in df.index})
    test_dates = all_dates[-252:] if len(all_dates) > 252 else all_dates
    if len(test_dates) < 5:
        raise RuntimeError("not enough trading days of market data")
    start_date = test_dates[0].isoformat()
    end_date = test_dates[-1].isoformat()

    strat = get_strategy(_config_for(entry))
    required = strat.required_symbols()
    subset = {s: bars_by_symbol[s] for s in required if s in bars_by_symbol}
    curve = strat.run(subset, start_date, end_date, INITIAL_CAPITAL) if subset else []

    from dashboard.backend.domain.strategy_testing.report import build_report

    report_curve = [{"date": str(row["timestamp"])[:10], "equity": row["equity"]} for row in curve]
    report = build_report(report_curve, INITIAL_CAPITAL)
    report["key"] = key
    report["name"] = entry.name
    report["window"] = {"start_date": start_date, "end_date": end_date}
    report["n_trades"] = strat.num_trades()
    return report


def build_export(key: str) -> Dict[str, Any]:
    """The Catalog's "Export" button: a re-shareable ``.strategy.json``
    package, same format the Manual page and Testing page use.

    Only a strategy added via the Testing page (``strategy: "sandboxed"``,
    with its Python source stored inline as ``code``) has real, re-runnable
    source to export. The other 11 built-in strategies here are Python
    classes using pandas against this app's own engine, not a standalone
    ``decide(price_history)`` script -- exporting their source verbatim would
    produce a file that looks uploadable but silently fails every check on
    the Testing page, or worse, half-runs under a different contract. For
    those, this returns a non-executable reference package (name,
    description, current parameter values) instead of fabricating code."""
    config = load_leaderboard_config()
    strat_entry = next((s for s in config.get("strategies", []) if s.get("id") == key), None)
    if strat_entry is None:
        raise ValueError(f"no strategy '{key}' in the catalog")

    from dashboard.backend.domain.strategy_testing.promote import build_export_package

    if strat_entry.get("strategy") == "sandboxed" and strat_entry.get("code"):
        return build_export_package(
            name=strat_entry.get("name") or key,
            description=strat_entry.get("description") or "",
            code=strat_entry["code"],
            source="strategy_catalog",
        )

    from dashboard.backend.domain.leaderboard.strategy_overrides import effective_params, schema_for

    return {
        "format": "newworldtrading-strategy-reference-v1",
        "executable": False,
        "name": strat_entry.get("name") or key,
        "description": _description_for(key, strat_entry.get("name") or key, strat_entry)["description"],
        "note": (
            "This is a built-in Strategy Catalog strategy implemented in the dashboard's own Python code, "
            "not a standalone decide(price_history) script -- it cannot be re-uploaded to the Testing page. "
            "This file documents its current configuration for reference/sharing."
        ),
        "parameter_schema": schema_for(key),
        "parameter_values": effective_params(key),
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


def append_placeholder_to_cache(*, key: str, name: str, source: str, description: str) -> None:
    """Make a just-added roster entry visible on the catalog page now, rather
    than after the 24h cache TTL. Mirror image of remove_strategy's cache
    eviction (same audit, 2026-08-29): without it "Add to Strategy" looked
    broken for a day. The real curve/metrics fill in on the next recompute."""
    cached = _load_cache()
    if not cached or not isinstance(cached.get("entries"), list):
        return
    if any(e.get("key") == key for e in cached["entries"]):
        return
    cached["entries"].append({
        "key": key,
        "name": name,
        "source": source,
        "description": description,
        "metrics": {
            "final": cached.get("initial_capital", 1000.0),
            "return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "n_trades": None,
            "note": "Just added -- performance fills in on the next catalog refresh.",
        },
        "equity_curve": [],
    })
    _save_cache(cached)


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
