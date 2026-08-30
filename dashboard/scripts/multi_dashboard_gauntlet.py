"""Multi-dashboard gauntlet: every deterministic strategy on the Options,
Futures, Forex, and Crypto dashboards, each run over the maximum window its
data source supports (~2 years -- Yahoo's ceiling for futures/forex
continuous dailies; matched elsewhere for cross-checkability).

Companion to community_strategy_lab_3y.py (the stocks gauntlet). Prediction
is deliberately absent: that dashboard has no backtest path by design --
prediction-market prices gap discretely on news, so the platform only
forward-tests those strategies against live Kalshi/Polymarket data, 5 real
days at a time. Nothing to gauntlet.

Excluded per domain: *_llm_agent (spends API money, nondeterministic) and
*_sandboxed (no uploaded code to run).

Resource discipline: strictly sequential, one strategy at a time, per-result
checkpoints (a rerun skips finished work). Options is the slow one on first
run -- its backtester probes Alpaca for which synthesized OCC contracts
really existed; probes are cached in SQLite so reruns are fast.

Usage:  python dashboard/scripts/multi_dashboard_gauntlet.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
from _bootstrap import ensure_repo_root
ensure_repo_root()

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / "dashboard" / ".env")

import numpy as np
import pandas as pd

INITIAL_CAPITAL = 1000.0
WINDOW_DAYS = 730  # ~2 years; Yahoo's practical ceiling for futures/forex dailies

OUT_DIR = Path(
    r"C:\Users\Trenton\AppData\Local\Temp\claude\C--Users-Trenton-Mission-Control-Alpaca-Trading"
    r"\77c8dc25-06fd-40a9-92e9-cfb030c35da5\scratchpad\multi_gauntlet"
)

EXCLUDE_SUFFIXES = ("_llm_agent", "_sandboxed")


def domain_jobs() -> List[Dict[str, Any]]:
    from dashboard.backend.domain.options.strategies.registry import (
        available_strategies as options_strategies,
    )
    from dashboard.backend.domain.futures.strategies.registry import (
        available_strategies as futures_strategies,
    )
    from dashboard.backend.domain.forex.strategies.registry import (
        available_strategies as forex_strategies,
    )
    from dashboard.backend.domain.crypto.strategies.registry import (
        available_strategies as crypto_strategies,
    )

    jobs = []
    for domain, avail in (
        ("options", options_strategies()),
        ("futures", futures_strategies()),
        ("forex", forex_strategies()),
        ("crypto", crypto_strategies()),
    ):
        for key, cls in sorted(avail.items()):
            if key.endswith(EXCLUDE_SUFFIXES):
                continue
            jobs.append({"domain": domain, "key": key, "cls": cls})
    return jobs


def metrics_from_curve(curve: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not curve:
        return {
            "final": INITIAL_CAPITAL, "return_pct": 0.0, "max_drawdown_pct": 0.0,
            "sharpe": 0.0, "cagr_pct": 0.0,
        }
    eq = np.array([r["equity"] for r in curve], dtype=float)
    peak = np.maximum.accumulate(eq)
    dd = float(((eq - peak) / peak).min() * 100)
    out = {
        "final": round(float(eq[-1]), 2),
        "return_pct": round((float(eq[-1]) / INITIAL_CAPITAL - 1) * 100, 2),
        "max_drawdown_pct": round(dd, 2),
    }
    rets = pd.Series(eq).pct_change().dropna()
    out["sharpe"] = round(float(rets.mean() / rets.std() * np.sqrt(252)), 2) if len(rets) > 5 and rets.std() > 0 else 0.0
    years = len(eq) / 252.0
    out["cagr_pct"] = round((float(eq[-1]) / float(eq[0])) ** (1 / years) - 1, 4) * 100 if years > 0 else 0.0
    out["cagr_pct"] = round(out["cagr_pct"], 2)
    return out


def sample_curve(curve: List[Dict[str, Any]], max_points: int = 260) -> List[Dict[str, Any]]:
    pts = [{"t": r["date"], "equity": round(r["equity"], 2)} for r in curve]
    if len(pts) <= max_points:
        return pts
    step = max(1, len(pts) // max_points)
    sampled = pts[::step]
    if sampled[-1] is not pts[-1]:
        sampled.append(pts[-1])
    return sampled


def first_doc_line(cls) -> str:
    doc = (cls.__doc__ or "").strip().splitlines()
    return doc[0].strip() if doc else ""


def main() -> None:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=WINDOW_DAYS)
    start_iso, end_iso = start.isoformat(), end.isoformat()
    print(f"[window] {start_iso} -> {end_iso} (~2 years)", flush=True)

    jobs = domain_jobs()
    print(f"[roster] {len(jobs)} deterministic strategies across 4 dashboards", flush=True)

    done = skipped = failed = 0
    for i, job in enumerate(jobs, 1):
        result_dir = OUT_DIR / "results" / job["domain"]
        result_dir.mkdir(parents=True, exist_ok=True)
        out_path = result_dir / f"{job['key']}.json"
        if out_path.exists():
            skipped += 1
            continue
        t0 = time.time()
        try:
            strat = job["cls"]({"strategy": job["key"], "id": job["key"], "name": job["key"]})
            curve = strat.run(start_iso, end_iso, INITIAL_CAPITAL)
            m = metrics_from_curve(curve)
            if curve and abs(m["final"] - INITIAL_CAPITAL) < 0.01 and len(curve) < 30:
                raise RuntimeError(f"suspicious flat/short curve ({len(curve)} points) — data likely missing")
            result = {
                "key": job["key"],
                "domain": job["domain"],
                "name": job["key"],
                "description": first_doc_line(job["cls"]),
                "metrics": m,
                "window": {"start": start_iso, "end": end_iso},
                "curve": sample_curve(curve),
            }
            out_path.write_text(json.dumps(result), encoding="utf-8")
            done += 1
            print(f"[{i}/{len(jobs)}] {job['domain']:8} {job['key']:24} "
                  f"final=${m['final']:<9} ret={m['return_pct']:>7}%  dd={m['max_drawdown_pct']:>7}%  "
                  f"sharpe={m['sharpe']:>5}  ({time.time()-t0:.1f}s)", flush=True)
        except Exception as exc:
            failed += 1
            out_path.with_suffix(".error").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
            print(f"[{i}/{len(jobs)}] {job['domain']:8} {job['key']:24} FAILED: "
                  f"{type(exc).__name__}: {str(exc)[:140]}", flush=True)

    print(f"\n[done] ran={done} skipped(existing)={skipped} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
