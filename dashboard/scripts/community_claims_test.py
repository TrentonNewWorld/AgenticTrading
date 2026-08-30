"""Test community-board 'daily profit' strategy claims against real data.

Four claims with concrete, portable rules:
  1. Turnaround Tuesday (quant blogs/boards): Monday closes red -> buy the
     close, sell on the first up-close (max hold 5 days). SPY, 3y daily.
  2. Martingale dip-doubler (forex/EA forums' 'consistent daily profit'
     flagship): buy after a down day, DOUBLE exposure each further down day,
     cash out entirely on the first green day. Exposure is capped at 100% of
     the wallet -- a real account can't double past its own equity, which is
     precisely the detail the forum math omits.
  3. Breakout-day continuation ('gap and go', daily approximation): if
     yesterday closed above the prior 10 days' high, hold today.
  4. Grid trading (forex forums): EURUSD, buy 500 units each -0.5% step,
     close each level at +0.5% above its own entry. Run through the real
     forex backtester (2y).

Intraday claims (ORB, VWAP scalping, 0DTE condors, power hour) are NOT
testable on daily bars and are deliberately absent rather than approximated
into meaninglessness.
"""

from __future__ import annotations

import json
import pickle
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
from _bootstrap import ensure_repo_root
ensure_repo_root()

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / "dashboard" / ".env")

import numpy as np
import pandas as pd

from dashboard.backend.domain.leaderboard.strategies._signal_engine import (
    DailyHistory,
    run_daily_signal_strategy,
)

SP = Path(
    r"C:\Users\Trenton\AppData\Local\Temp\claude\C--Users-Trenton-Mission-Control-Alpaca-Trading"
    r"\77c8dc25-06fd-40a9-92e9-cfb030c35da5\scratchpad"
)
OUT = SP / "community_claims"
OUT.mkdir(parents=True, exist_ok=True)


def metrics(curve, capital=1000.0):
    if not curve:
        return {}
    eq = np.array([r["equity"] for r in curve], dtype=float)
    rets = eq[1:] / eq[:-1] - 1
    peak = np.maximum.accumulate(eq)
    streak = cur = 0
    for r in rets:
        cur = cur + 1 if r < 0 else 0
        streak = max(streak, cur)
    n_days = len(eq)
    return {
        "final": round(float(eq[-1]), 2),
        "return_pct": round((eq[-1] / capital - 1) * 100, 2),
        "avg_daily_pct": round(float((eq[-1] / capital) ** (1 / max(n_days - 1, 1)) - 1) * 100, 4),
        "green_days_pct": round(float((rets > 0).mean() * 100), 1),
        "worst_day_pct": round(float(rets.min() * 100), 2),
        "max_drawdown_pct": round(float(((eq - peak) / peak).min() * 100), 2),
        "max_losing_streak": int(streak),
        "days": n_days,
    }


# ---------------------------------------------------------------------------
# Stocks-side claims on SPY (3y window, same engine as the gauntlet)
# ---------------------------------------------------------------------------

def wf_turnaround_tuesday():
    state = {"held_since": None}

    def weight_fn(h: DailyHistory, cur_date, day_index):
        close = h.close["SPY"].dropna()
        if len(close) < 3:
            return {}
        held = state["held_since"] is not None
        if held:
            # sell on first up-close (yesterday closed above entry-day close),
            # or after 5 days
            since = day_index - state["held_since"]
            if close.iloc[-1] > close.iloc[-2] or since >= 5:
                state["held_since"] = None
                return {}
            return {"SPY": 1.0}
        # Monday effect: cur_date is Tuesday and yesterday (Monday) closed red
        if cur_date.weekday() == 1 and close.iloc[-1] < close.iloc[-2]:
            state["held_since"] = day_index
            return {"SPY": 1.0}
        return {}
    return weight_fn


def wf_martingale_dip():
    state = {"level": 0}

    def weight_fn(h: DailyHistory, cur_date, day_index):
        close = h.close["SPY"].dropna()
        if len(close) < 2:
            return {}
        down = close.iloc[-1] < close.iloc[-2]
        if state["level"] == 0:
            if down:
                state["level"] = 1
                return {"SPY": 0.125}
            return {}
        if down:
            state["level"] = min(state["level"] + 1, 4)  # 12.5 -> 25 -> 50 -> 100%
        else:
            state["level"] = 0
            return {}
        return {"SPY": min(0.125 * (2 ** (state["level"] - 1)), 1.0)}
    return weight_fn


def wf_breakout_continuation():
    def weight_fn(h: DailyHistory, cur_date, day_index):
        close = h.close["SPY"].dropna()
        high = h.high["SPY"].dropna()
        if len(close) < 12:
            return {}
        if close.iloc[-1] > high.iloc[-11:-1].max():
            return {"SPY": 1.0}
        return {}
    return weight_fn


def run_stock_claims():
    bars = pickle.loads((SP / "strategy_lab_3y" / "bars_cache.pkl").read_bytes())["bars"]
    spy = {"SPY": bars["SPY"]}
    all_dates = sorted({d.date() for d in bars["SPY"].index})
    test_dates = all_dates[-756:]
    start, end = test_dates[0].isoformat(), test_dates[-1].isoformat()

    results = {}
    for name, claim, factory in [
        ("turnaround_tuesday", "boards claim a reliable weekly edge", wf_turnaround_tuesday),
        ("martingale_dip_doubler", "EA forums claim consistent daily profit, 'can't lose'", wf_martingale_dip),
        ("breakout_continuation", "'gap and go' momentum, daily approximation", wf_breakout_continuation),
    ]:
        curve, n_trades = run_daily_signal_strategy(spy, start, end, 1000.0, factory(), 1, lot_size=10.0)
        m = metrics(curve)
        m["n_trades"] = n_trades
        m["claim"] = claim
        m["window"] = f"{start}..{end}"
        results[name] = m
        print(f"{name:26} final=${m['final']:<9} avg/day={m['avg_daily_pct']}%  "
              f"green={m['green_days_pct']}%  dd={m['max_drawdown_pct']}%", flush=True)
    return results


# ---------------------------------------------------------------------------
# Forex grid claim, through the real forex backtester (2y)
# ---------------------------------------------------------------------------

GRID_CODE = '''
def decide_forex(as_of, positions, quotes, account):
    q = quotes.get("EURUSD=X")
    if not q:
        return []
    price = q.get("price")
    if not price or price <= 0:
        return []
    intents = []
    # close any level that is >= +0.5% above its entry is not visible here
    # (positions carry no entry price), so use the grid anchor convention:
    # one open position per 0.5% band below the 20-day high water mark.
    closes = q.get("closes") or []
    if len(closes) < 2:
        return []
    anchor = max(closes[-20:]) if len(closes) >= 20 else max(closes)
    # desired number of open grid levels = how many 0.5% steps price sits
    # below the anchor (capped at 6 levels = 3000 units ~ full wallet)
    drop = (anchor - price) / anchor
    want_levels = min(int(drop / 0.005), 6) if drop > 0 else 0
    have_levels = len(positions)
    if want_levels > have_levels:
        intents.append({"action": "open", "symbol": "EURUSD=X", "side": "buy", "qty": 500})
    elif want_levels < have_levels:
        intents.append({"action": "close", "symbol": "EURUSD=X", "side": "sell", "qty": 500})
    return intents
'''


def run_grid_claim():
    from dashboard.backend.domain.forex.backtester import run_backtest

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=730)
    curve = run_backtest(GRID_CODE, ["EURUSD=X"], start, end, 1000.0)
    m = metrics(curve)
    m["claim"] = "forex forums: grid = consistent daily profit"
    m["window"] = f"{start.isoformat()}..{end.isoformat()}"
    print(f"{'forex_grid':26} final=${m.get('final','?'):<9} avg/day={m.get('avg_daily_pct','?')}%  "
          f"green={m.get('green_days_pct','?')}%  dd={m.get('max_drawdown_pct','?')}%", flush=True)
    return {"forex_grid": m}


if __name__ == "__main__":
    out = run_stock_claims()
    out.update(run_grid_claim())
    (OUT / "results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\nsaved:", OUT / "results.json")
