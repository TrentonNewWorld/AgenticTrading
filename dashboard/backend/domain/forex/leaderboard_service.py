"""Forex Competition + Live Trading Leaderboards -- ranked views of the
Forex Strategy Catalog's own precomputed metrics. Mirrors
domain/futures/leaderboard_service.py exactly -- see that module and
domain/options/leaderboard_service.py's docstrings for the full reasoning
(contest = most recently completed full year, viewing-only; live = fixed-
anchor forward-tracking window, not derived from "today").
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from dashboard.backend.domain.forex import catalog as forex_catalog
from dashboard.backend.domain.forex.strategies import get_strategy
from dashboard.backend.domain.leaderboard.baselines import contest_window_for_year
from dashboard.backend.paths import REPO_ROOT

VALID_PERIODS = ("contest", "live")

LIVE_CACHE_PATH = REPO_ROOT / "dashboard" / "storage" / "data" / "forex_live_leaderboard_cache.json"
LIVE_CACHE_TTL_HOURS = 24


def _rank_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = sorted(entries, key=lambda e: e["metrics"].get("return_pct", 0.0), reverse=True)
    for i, entry in enumerate(ranked, start=1):
        entry["rank"] = i
    return ranked


def _compute_for_window(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    entries_out = []
    for entry in forex_catalog._catalog_roster():
        try:
            strat = get_strategy(forex_catalog._config_for(entry))
            curve = strat.run(start_date, end_date, forex_catalog.INITIAL_CAPITAL)
            metrics = forex_catalog._metrics(curve)
            sampled = curve[::3] if len(curve) > 120 else curve
            if curve and sampled[-1] is not curve[-1]:
                sampled = sampled + [curve[-1]]
        except Exception as exc:
            metrics = {"final": forex_catalog.INITIAL_CAPITAL, "return_pct": 0.0, "max_drawdown_pct": 0.0}
            sampled = []
            metrics["error"] = str(exc)[:200]

        entries_out.append({
            "key": entry.key, "name": entry.name, "source": entry.source,
            "description": entry.description, "metrics": metrics,
            "equity_curve": [{"t": row["date"], "equity": row["equity"]} for row in sampled],
        })
    return _rank_entries(entries_out)


def _load_live_cache() -> Dict[str, Any] | None:
    if not LIVE_CACHE_PATH.exists():
        return None
    try:
        with LIVE_CACHE_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_live_cache(payload: Dict[str, Any]) -> None:
    LIVE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LIVE_CACHE_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f)


def _live_cache_is_stale(payload: Dict[str, Any], live_end: str) -> bool:
    if payload.get("window", {}).get("end_date") != live_end:
        return True
    computed_at = payload.get("computed_at")
    if not computed_at:
        return True
    try:
        ts = datetime.fromisoformat(computed_at)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - ts > timedelta(hours=LIVE_CACHE_TTL_HOURS)


def _live_season_start() -> Any:
    from datetime import date as date_cls

    config = forex_catalog.load_leaderboard_config()
    raw = config.get("live_season_start")
    if not raw:
        return None
    try:
        return date_cls.fromisoformat(raw)
    except ValueError:
        return None


def get_leaderboard(period: str = "contest", *, force_refresh: bool = False) -> Dict[str, Any]:
    period = period if period in VALID_PERIODS else "contest"
    today = datetime.now(timezone.utc).date()

    if period == "contest":
        contest_start, contest_end = contest_window_for_year(today)
        catalog_payload = forex_catalog.get_strategy_catalog(force_refresh=force_refresh)
        return {
            "period": "contest",
            "window": catalog_payload.get("window") or {
                "start_date": contest_start.isoformat(), "end_date": contest_end.isoformat(),
            },
            "entries": _rank_entries(list(catalog_payload.get("entries") or [])),
            "computed_at": catalog_payload.get("computed_at"),
            "status": "final",
        }

    live_start = _live_season_start()
    if live_start is None:
        return {
            "period": "live", "window": {"start_date": None, "end_date": None},
            "entries": [], "computed_at": datetime.now(timezone.utc).isoformat(),
            "status": "preview",
        }
    live_window_end_cap = live_start + timedelta(days=365)
    live_end = min(today - timedelta(days=1), live_window_end_cap)
    if live_end < live_start:
        return {
            "period": "live",
            "window": {"start_date": live_start.isoformat(), "end_date": live_start.isoformat()},
            "entries": [], "computed_at": datetime.now(timezone.utc).isoformat(),
            "status": "preview",
        }

    live_end_str = live_end.isoformat()
    if not force_refresh:
        cached = _load_live_cache()
        if cached and not _live_cache_is_stale(cached, live_end_str):
            return cached

    entries = _compute_for_window(live_start.isoformat(), live_end_str)
    payload = {
        "period": "live",
        "window": {"start_date": live_start.isoformat(), "end_date": live_end_str},
        "entries": entries,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "status": "live",
    }
    if entries:
        _save_live_cache(payload)
        return payload

    cached = _load_live_cache()
    return cached or payload
