"""Community Strategy Lab — 3-year backtest of every community-reputed strategy.

Runs every deterministic strategy in the leaderboard registry (the ones ported
from freqtrade/QuantConnect/TradingAgents community sources) PLUS twelve
TradingView-community classics implemented below, all against the same three
years of real Alpaca daily bars, through the exact same portfolio engine the
Strategy Catalog uses (`run_daily_signal_strategy`, $1,000 start, $10 lots).

Laptop-resource discipline, by design:
  * ONE market-data fetch, cached to disk — every strategy reuses it. Rerun
    costs zero API calls until the cache date rolls.
  * Strictly sequential: one strategy at a time, one process, daily bars only
    (the whole dataset is ~1,200 rows x 33 symbols — a few MB).
  * Checkpointed: each strategy's result lands in its own JSON as it
    finishes, so an interruption loses at most one strategy's work and a
    rerun skips everything already done.
  * Excluded: llm_agent and sandboxed (need an LLM key / uploaded code —
    not deterministic, and the LLM one would spend real API money).

Usage:  python dashboard/scripts/community_strategy_lab_3y.py
"""

from __future__ import annotations

import json
import os
import pickle
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
from _bootstrap import ensure_repo_root
ensure_repo_root()

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / "dashboard" / ".env")

import numpy as np
import pandas as pd

from dashboard.backend.domain.leaderboard.catalog import _fetch_daily_bars
from dashboard.backend.domain.leaderboard.strategies import get_strategy
from dashboard.backend.domain.leaderboard.strategies.registry import _STRATEGY_CLASSES
from dashboard.backend.domain.leaderboard.strategies._indicators import (
    adx,
    bollinger,
    rsi,
)
from dashboard.backend.domain.leaderboard.strategies._signal_engine import (
    DailyHistory,
    make_entry_exit_weight_fn,
    run_daily_signal_strategy,
)
from dashboard.backend.infrastructure.llm.validator import DJIA_30

INITIAL_CAPITAL = 1000.0
LOT_SIZE = 10.0
TRADING_DAYS_3Y = 756  # 3 x 252
WARMUP_CALENDAR_DAYS = 1500  # 3y window + ~300 trading days for 252d lookbacks

OUT_DIR = Path(os.environ.get(
    "STRATEGY_LAB_OUT",
    r"C:\Users\Trenton\AppData\Local\Temp\claude\C--Users-Trenton-Mission-Control-Alpaca-Trading"
    r"\77c8dc25-06fd-40a9-92e9-cfb030c35da5\scratchpad\strategy_lab_3y",
))
RESULTS_DIR = OUT_DIR / "results"
BARS_CACHE = OUT_DIR / "bars_cache.pkl"

EXCLUDED_KEYS = {"llm_agent", "sandboxed"}

UNIVERSE = sorted(set(DJIA_30) | {"SPY", "DIA", "QQQ"})


# ---------------------------------------------------------------------------
# Twelve TradingView-community classics not already in the registry.
# Each is (key, name, community source note, weight_fn factory, universe).
# All run through the same engine/lots as the registered strategies.
# ---------------------------------------------------------------------------

def _tail(df: pd.DataFrame, sym: str, n: int) -> pd.Series:
    s = df[sym].dropna()
    return s.iloc[-n:] if len(s) >= n else s


def wf_rsi2_connors():
    """Larry Connors RSI-2: buy deep RSI(2) dips above the 200-day SMA."""
    def entry(h: DailyHistory, sym: str) -> bool:
        close = h.close[sym].dropna()
        if len(close) < 210:
            return False
        sma200 = close.iloc[-200:].mean()
        r = rsi(close.to_frame("x"), 2)["x"].iloc[-1]
        return close.iloc[-1] > sma200 and r < 10

    def exit_(h: DailyHistory, sym: str) -> bool:
        close = h.close[sym].dropna()
        if len(close) < 3:
            return False
        r = rsi(close.to_frame("x"), 2)["x"].iloc[-1]
        return r > 65

    return make_entry_exit_weight_fn(entry, exit_, ["SPY"], max_positions=1, min_history=210)


def wf_golden_cross():
    """50/200 SMA golden cross on SPY; cash on the death cross."""
    def weight_fn(h: DailyHistory, cur_date, day_index):
        close = h.close["SPY"].dropna()
        if len(close) < 200:
            return {}
        if close.iloc[-50:].mean() > close.iloc[-200:].mean():
            return {"SPY": 1.0}
        return {}
    return weight_fn


def wf_donchian_turtle():
    """Turtle-style Donchian channel: 20-day-high breakout in, 10-day-low out."""
    def entry(h: DailyHistory, sym: str) -> bool:
        close = h.close[sym].dropna()
        high = h.high[sym].dropna()
        if len(close) < 21:
            return False
        return close.iloc[-1] >= high.iloc[-21:-1].max()

    def exit_(h: DailyHistory, sym: str) -> bool:
        close = h.close[sym].dropna()
        low = h.low[sym].dropna()
        if len(close) < 11:
            return False
        return close.iloc[-1] <= low.iloc[-11:-1].min()

    return make_entry_exit_weight_fn(entry, exit_, list(DJIA_30), max_positions=8, min_history=21)


def wf_bollinger_meanrev():
    """Buy closes below the lower Bollinger(20,2) band, exit at the midline."""
    def entry(h: DailyHistory, sym: str) -> bool:
        close = h.close[sym].dropna()
        if len(close) < 21:
            return False
        upper, mid, lower, _pct_b = bollinger(close.to_frame("x"), 20, 2.0)
        return close.iloc[-1] < lower["x"].iloc[-1]

    def exit_(h: DailyHistory, sym: str) -> bool:
        close = h.close[sym].dropna()
        if len(close) < 21:
            return False
        upper, mid, lower, _pct_b = bollinger(close.to_frame("x"), 20, 2.0)
        return close.iloc[-1] >= mid["x"].iloc[-1]

    return make_entry_exit_weight_fn(entry, exit_, list(DJIA_30), max_positions=8, min_history=21)


def wf_ichimoku_trend():
    """Simplified Ichimoku: hold SPY while price is above the cloud."""
    def weight_fn(h: DailyHistory, cur_date, day_index):
        high = h.high["SPY"].dropna()
        low = h.low["SPY"].dropna()
        close = h.close["SPY"].dropna()
        if len(close) < 78:  # 52 + 26 shift
            return {}
        conv = (high.iloc[-9:].max() + low.iloc[-9:].min()) / 2
        base = (high.iloc[-26:].max() + low.iloc[-26:].min()) / 2
        # Cloud today = spans computed 26 days ago
        h26, l26, c26 = high.iloc[:-26], low.iloc[:-26], close.iloc[:-26]
        span_a = (
            (h26.iloc[-9:].max() + l26.iloc[-9:].min()) / 2
            + (h26.iloc[-26:].max() + l26.iloc[-26:].min()) / 2
        ) / 2
        span_b = (h26.iloc[-52:].max() + l26.iloc[-52:].min()) / 2
        cloud_top = max(span_a, span_b)
        if close.iloc[-1] > cloud_top and conv > base:
            return {"SPY": 1.0}
        return {}
    return weight_fn


def wf_stochastic_cross():
    """Stochastic %K/%D: oversold cross up in, overbought out."""
    def _k(h: DailyHistory, sym: str, n: int = 14) -> pd.Series:
        close = h.close[sym].dropna()
        high = h.high[sym].reindex(close.index)
        low = h.low[sym].reindex(close.index)
        ll = low.rolling(n).min()
        hh = high.rolling(n).max()
        return (100 * (close - ll) / (hh - ll)).dropna()

    def entry(h: DailyHistory, sym: str) -> bool:
        k = _k(h, sym)
        if len(k) < 5:
            return False
        d = k.rolling(3).mean()
        return (
            k.iloc[-1] > d.iloc[-1]
            and k.iloc[-2] <= d.iloc[-2]
            and k.iloc[-1] < 30
        )

    def exit_(h: DailyHistory, sym: str) -> bool:
        k = _k(h, sym)
        return len(k) >= 1 and k.iloc[-1] > 80

    return make_entry_exit_weight_fn(entry, exit_, list(DJIA_30), max_positions=8, min_history=20)


def wf_williams_r():
    """Williams %R(14): buy washed-out readings, exit on recovery."""
    def _wr(h: DailyHistory, sym: str, n: int = 14) -> float:
        close = h.close[sym].dropna()
        high = h.high[sym].reindex(close.index)
        low = h.low[sym].reindex(close.index)
        if len(close) < n:
            return 0.0
        hh = high.iloc[-n:].max()
        ll = low.iloc[-n:].min()
        if hh == ll:
            return 0.0
        return float(-100 * (hh - close.iloc[-1]) / (hh - ll))

    def entry(h: DailyHistory, sym: str) -> bool:
        return _wr(h, sym) < -80

    def exit_(h: DailyHistory, sym: str) -> bool:
        return _wr(h, sym) > -20

    return make_entry_exit_weight_fn(entry, exit_, list(DJIA_30), max_positions=8, min_history=15)


def wf_cci_100():
    """CCI(20) +100 momentum: ride strong trends, exit when CCI goes negative."""
    def _cci(h: DailyHistory, sym: str, n: int = 20) -> float:
        close = h.close[sym].dropna()
        high = h.high[sym].reindex(close.index)
        low = h.low[sym].reindex(close.index)
        if len(close) < n:
            return 0.0
        tp = ((high + low + close) / 3).iloc[-n:]
        mean = tp.mean()
        mad = (tp - mean).abs().mean()
        if mad == 0:
            return 0.0
        return float((tp.iloc[-1] - mean) / (0.015 * mad))

    def entry(h: DailyHistory, sym: str) -> bool:
        return _cci(h, sym) > 100

    def exit_(h: DailyHistory, sym: str) -> bool:
        return _cci(h, sym) < 0

    return make_entry_exit_weight_fn(entry, exit_, list(DJIA_30), max_positions=8, min_history=21)


def wf_adx_dmi():
    """ADX(14) > 25 with +DI over -DI: trade only confirmed trends."""
    def _dmi(h: DailyHistory, sym: str):
        close = h.close[sym].dropna().to_frame("x")
        high = h.high[sym].reindex(close.index).to_frame("x")
        low = h.low[sym].reindex(close.index).to_frame("x")
        a, plus, minus = adx(high, low, close, 14)
        return a["x"].iloc[-1], plus["x"].iloc[-1], minus["x"].iloc[-1]

    def entry(h: DailyHistory, sym: str) -> bool:
        if len(h.close[sym].dropna()) < 30:
            return False
        a, p, m = _dmi(h, sym)
        return bool(a > 25 and p > m)

    def exit_(h: DailyHistory, sym: str) -> bool:
        if len(h.close[sym].dropna()) < 30:
            return False
        a, p, m = _dmi(h, sym)
        return bool(p < m)

    return make_entry_exit_weight_fn(entry, exit_, list(DJIA_30), max_positions=8, min_history=30)


def wf_week52_high():
    """52-week-high momentum: hold the 8 Dow names closest to their yearly high."""
    def weight_fn(h: DailyHistory, cur_date, day_index):
        if len(h) < 60 or day_index % 21 != 0:
            weight_fn.last = getattr(weight_fn, "last", {})
            return weight_fn.last
        scores = {}
        for sym in DJIA_30:
            close = h.close[sym].dropna()
            if len(close) < 60:
                continue
            window = close.iloc[-252:] if len(close) >= 252 else close
            scores[sym] = close.iloc[-1] / window.max()
        top = sorted(scores, key=scores.get, reverse=True)[:8]
        weight_fn.last = {s: 1.0 / len(top) for s in top} if top else {}
        return weight_fn.last
    return weight_fn


def wf_dual_momentum():
    """Antonacci-style absolute momentum: SPY when its 12-month return is
    positive, cash otherwise. Monthly check."""
    def weight_fn(h: DailyHistory, cur_date, day_index):
        close = h.close["SPY"].dropna()
        if len(close) < 253:
            return {}
        if day_index % 21 != 0:
            return getattr(weight_fn, "last", {})
        ret_12m = close.iloc[-1] / close.iloc[-253] - 1
        weight_fn.last = {"SPY": 1.0} if ret_12m > 0 else {}
        return weight_fn.last
    return weight_fn


def wf_ema_ribbon():
    """EMA ribbon 8/21/55 on SPY: hold only while the ribbon is stacked bullish."""
    def weight_fn(h: DailyHistory, cur_date, day_index):
        close = h.close["SPY"].dropna()
        if len(close) < 60:
            return {}
        e8 = close.ewm(span=8, adjust=False).mean().iloc[-1]
        e21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
        e55 = close.ewm(span=55, adjust=False).mean().iloc[-1]
        if e8 > e21 > e55:
            return {"SPY": 1.0}
        return {}
    return weight_fn


def wf_macd_spy():
    """Classic MACD(12,26,9) signal-line cross on SPY."""
    def weight_fn(h: DailyHistory, cur_date, day_index):
        close = h.close["SPY"].dropna()
        if len(close) < 40:
            return {}
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9, adjust=False).mean()
        if macd_line.iloc[-1] > signal.iloc[-1]:
            return {"SPY": 1.0}
        return {}
    return weight_fn


COMMUNITY_CLASSICS = [
    ("tv_rsi2_connors", "RSI-2 (Connors)", "Larry Connors classic; endlessly re-published on TradingView",
     wf_rsi2_connors, ["SPY"]),
    ("tv_golden_cross", "Golden Cross 50/200", "The most famous trend signal in retail trading",
     wf_golden_cross, ["SPY"]),
    ("tv_donchian_turtle", "Donchian Breakout (Turtle)", "The original Turtle Traders channel breakout",
     wf_donchian_turtle, list(DJIA_30)),
    ("tv_bollinger_meanrev", "Bollinger Band Mean Reversion", "Buy the lower band, sell the middle — a TV staple",
     wf_bollinger_meanrev, list(DJIA_30)),
    ("tv_ichimoku", "Ichimoku Cloud Trend", "Price-above-cloud trend following, simplified",
     wf_ichimoku_trend, ["SPY"]),
    ("tv_stochastic", "Stochastic Oversold Cross", "%K/%D cross out of oversold — classic oscillator play",
     wf_stochastic_cross, list(DJIA_30)),
    ("tv_williams_r", "Williams %R Reversal", "Larry Williams' washed-out reversal indicator",
     wf_williams_r, list(DJIA_30)),
    ("tv_cci_100", "CCI +100 Momentum", "Donald Lambert's CCI ridden as a trend trigger",
     wf_cci_100, list(DJIA_30)),
    ("tv_adx_dmi", "ADX/DMI Trend Filter", "Wilder's directional system: only trade confirmed trends",
     wf_adx_dmi, list(DJIA_30)),
    ("tv_52w_high", "52-Week High Momentum", "Academic + community favorite: strength near yearly highs",
     wf_week52_high, list(DJIA_30)),
    ("tv_dual_momentum", "Dual Momentum (absolute)", "Gary Antonacci's GEM, equity/cash leg",
     wf_dual_momentum, ["SPY"]),
    ("tv_ema_ribbon", "EMA Ribbon 8/21/55", "Stacked-EMA trend riding, a TradingView perennial",
     wf_ema_ribbon, ["SPY"]),
    ("tv_macd_cross", "MACD Signal Cross (SPY)", "The single most-published strategy on TradingView",
     wf_macd_spy, ["SPY"]),
]


# ---------------------------------------------------------------------------

def sharpe_and_cagr(curve: List[Dict[str, Any]]) -> Dict[str, float]:
    if len(curve) < 10:
        return {"sharpe": 0.0, "cagr_pct": 0.0}
    eq = pd.Series([r["equity"] for r in curve], dtype=float)
    rets = eq.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
    years = len(eq) / 252.0
    cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    return {"sharpe": round(sharpe, 2), "cagr_pct": round(cagr, 2)}


def base_metrics(curve: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not curve:
        return {"final": INITIAL_CAPITAL, "return_pct": 0.0, "max_drawdown_pct": 0.0}
    eq = np.array([r["equity"] for r in curve], dtype=float)
    peak = np.maximum.accumulate(eq)
    dd = float(((eq - peak) / peak).min() * 100)
    return {
        "final": round(float(eq[-1]), 2),
        "return_pct": round((float(eq[-1]) / INITIAL_CAPITAL - 1) * 100, 2),
        "max_drawdown_pct": round(dd, 2),
    }


def sample_curve(curve: List[Dict[str, Any]], max_points: int = 260) -> List[Dict[str, Any]]:
    if len(curve) <= max_points:
        return [{"t": r["timestamp"], "equity": round(r["equity"], 2)} for r in curve]
    step = max(1, len(curve) // max_points)
    sampled = curve[::step]
    if sampled[-1] is not curve[-1]:
        sampled = sampled + [curve[-1]]
    return [{"t": r["timestamp"], "equity": round(r["equity"], 2)} for r in sampled]


def first_doc_line(cls) -> str:
    doc = (cls.__doc__ or "").strip().splitlines()
    return doc[0].strip() if doc else ""


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # ---- data: one fetch, cached ------------------------------------------
    cache_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bars_by_symbol = None
    if BARS_CACHE.exists():
        try:
            cached = pickle.loads(BARS_CACHE.read_bytes())
            if cached.get("date") == cache_key:
                bars_by_symbol = cached["bars"]
                print(f"[data] using cached bars ({cache_key})", flush=True)
        except Exception:
            bars_by_symbol = None
    if bars_by_symbol is None:
        print(f"[data] fetching {len(UNIVERSE)} symbols x {WARMUP_CALENDAR_DAYS}d from Alpaca...", flush=True)
        end = datetime.now(timezone.utc) - timedelta(days=1)
        bars_by_symbol = _fetch_daily_bars(UNIVERSE, end, lookback_days=WARMUP_CALENDAR_DAYS)
        if not bars_by_symbol:
            print("[data] FETCH FAILED — no bars. Aborting.", flush=True)
            sys.exit(1)
        BARS_CACHE.write_bytes(pickle.dumps({"date": cache_key, "bars": bars_by_symbol}))
    rows = {s: len(df) for s, df in bars_by_symbol.items()}
    print(f"[data] {len(bars_by_symbol)} symbols, {min(rows.values())}-{max(rows.values())} rows each", flush=True)

    all_dates = sorted({d.date() for df in bars_by_symbol.values() for d in df.index})
    test_dates = all_dates[-TRADING_DAYS_3Y:]
    start_date, end_date = test_dates[0].isoformat(), test_dates[-1].isoformat()
    print(f"[window] {start_date} -> {end_date} ({len(test_dates)} trading days)", flush=True)

    # ---- roster ------------------------------------------------------------
    jobs: List[Dict[str, Any]] = []
    for cls in _STRATEGY_CLASSES:
        if cls.key in EXCLUDED_KEYS:
            continue
        jobs.append({"key": cls.key, "name": cls.key, "source": "registry",
                     "description": first_doc_line(cls), "cls": cls})
    for key, name, desc, factory, universe in COMMUNITY_CLASSICS:
        jobs.append({"key": key, "name": name, "source": "tradingview-classic",
                     "description": desc, "factory": factory, "universe": universe})

    print(f"[roster] {len(jobs)} strategies ({len(jobs) - len(COMMUNITY_CLASSICS)} registry + "
          f"{len(COMMUNITY_CLASSICS)} TradingView classics)", flush=True)

    done = skipped = failed = 0
    for i, job in enumerate(jobs, 1):
        out_path = RESULTS_DIR / f"{job['key']}.json"
        if out_path.exists():
            skipped += 1
            continue
        t0 = time.time()
        try:
            if "cls" in job:
                strat = get_strategy({"strategy": job["key"], "id": job["key"], "name": job["key"]})
                subset = {s: bars_by_symbol[s] for s in strat.required_symbols() if s in bars_by_symbol}
                curve = strat.run(subset, start_date, end_date, INITIAL_CAPITAL) if subset else []
                n_trades = strat.num_trades()
            else:
                subset = {s: bars_by_symbol[s] for s in job["universe"] if s in bars_by_symbol}
                curve, n_trades = run_daily_signal_strategy(
                    subset, start_date, end_date, INITIAL_CAPITAL,
                    job["factory"](), rebalance_every_days=1, lot_size=LOT_SIZE,
                )
            metrics = base_metrics(curve)
            metrics.update(sharpe_and_cagr(curve))
            metrics["n_trades"] = n_trades
            result = {
                "key": job["key"], "name": job["name"], "source": job["source"],
                "description": job["description"], "metrics": metrics,
                "window": {"start": start_date, "end": end_date},
                "curve": sample_curve(curve),
            }
            out_path.write_text(json.dumps(result), encoding="utf-8")
            done += 1
            print(f"[{i}/{len(jobs)}] {job['key']:24} final=${metrics['final']:<9} "
                  f"ret={metrics['return_pct']:>7}%  dd={metrics['max_drawdown_pct']:>7}%  "
                  f"sharpe={metrics.get('sharpe', 0):>5}  ({time.time()-t0:.1f}s)", flush=True)
        except Exception as exc:
            failed += 1
            out_path.with_suffix(".error").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
            print(f"[{i}/{len(jobs)}] {job['key']:24} FAILED: {type(exc).__name__}: {str(exc)[:120]}", flush=True)

    print(f"\n[done] ran={done} skipped(existing)={skipped} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
