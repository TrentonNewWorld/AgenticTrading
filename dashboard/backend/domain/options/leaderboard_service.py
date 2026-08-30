"""Options Competition + Live Trading Leaderboards -- ranked views of the
Options Strategy Catalog's own precomputed metrics.

Sub-phase 9 of the Options-dashboard plan. Per the user's redefinition, a
"leaderboard" here is not a separate LLM-tournament subsystem (the stocks
Competition Leaderboard's original purpose) -- it's simply the Options
Strategy Catalog's own strategies, ranked, over two different windows:

* ``"contest"`` (Competition): the most recently completed full year
  (``contest_window_for_year()``), viewing-only. Reuses
  ``domain.options.catalog.get_strategy_catalog()`` directly (same window,
  same cache) rather than recomputing -- the catalog and the Competition
  board were never two independently-computed things to begin with.
* ``"live"`` (Live Trading): tracks from a **fixed** anchor date
  (``leaderboard_options.json``'s ``live_season_start``) forward, up to 1
  year, through yesterday (the latest day the backtester can compute).
  Deliberately fixed, not derived from "today" the way the contest window
  is: an early draft of this module computed the live start as
  ``contest_window_for_year(today)``'s own end + 1 day, which -- because
  the contest window is itself defined relative to "today" -- always
  evaluated to exactly "today" no matter how much real time had passed,
  producing a permanently empty window. A live season needs a start date
  that does not itself roll forward with the clock, or elapsed time can
  never accumulate. This needs no separate "advance" engine the way the
  Stocks Live Trading board's hourly paper-trading simulation does: the
  Options backtester can compute any historical window on demand, so "live"
  is simply a fresh computation over a widening date range each time. Its
  own small JSON cache (mirroring the catalog's) keeps repeated page loads
  from re-running every strategy's backtest.

LLM-model entries are not included on either board for Options (none exist
in the Options Strategy Catalog roster) -- the architecture doesn't block
adding one later (a strategy config would resolve through the same
registry/ranking path any other entry does), it just isn't built now.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from dashboard.backend.domain.leaderboard.baselines import contest_window_for_year
from dashboard.backend.domain.options import catalog as options_catalog
from dashboard.backend.domain.options.strategies import get_strategy
from dashboard.backend.paths import REPO_ROOT

VALID_PERIODS = ("contest", "live")

LIVE_CACHE_PATH = REPO_ROOT / "dashboard" / "storage" / "data" / "options_live_leaderboard_cache.json"
LIVE_CACHE_TTL_HOURS = 24


def _rank_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = sorted(entries, key=lambda e: e["metrics"].get("return_pct", 0.0), reverse=True)
    for i, entry in enumerate(ranked, start=1):
        entry["rank"] = i
    return ranked


def _compute_for_window(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    entries_out = []
    for entry in options_catalog._catalog_roster():
        try:
            strat = get_strategy(options_catalog._config_for(entry))
            curve = strat.run(start_date, end_date, options_catalog.INITIAL_CAPITAL)
            metrics = options_catalog._metrics(curve)
            sampled = curve[::3] if len(curve) > 120 else curve
            if curve and sampled[-1] is not curve[-1]:
                sampled = sampled + [curve[-1]]
        except Exception as exc:  # a single strategy's data hiccup must not blank the whole board
            metrics = {"final": options_catalog.INITIAL_CAPITAL, "return_pct": 0.0, "max_drawdown_pct": 0.0}
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
    # Stale either by TTL, or -- more importantly for a daily-rolling window
    # -- if a new day has elapsed since this was cached (live_end advances
    # by one day at a time; a cache keyed to yesterday's end is wrong today).
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

    config = options_catalog.load_leaderboard_config()
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
        catalog_payload = options_catalog.get_strategy_catalog(force_refresh=force_refresh)
        return {
            "period": "contest",
            "window": catalog_payload.get("window") or {
                "start_date": contest_start.isoformat(), "end_date": contest_end.isoformat(),
            },
            "entries": _rank_entries(list(catalog_payload.get("entries") or [])),
            "computed_at": catalog_payload.get("computed_at"),
            "status": "final",
        }

    # period == "live" -- tracks forward from a fixed anchor, not from
    # today's own rolling contest window (see module docstring).
    live_start = _live_season_start()
    if live_start is None:
        return {
            "period": "live", "window": {"start_date": None, "end_date": None},
            "entries": [], "computed_at": datetime.now(timezone.utc).isoformat(),
            "status": "preview",
        }
    live_window_end_cap = live_start + timedelta(days=365)  # season length: 1 year
    live_end = min(today - timedelta(days=1), live_window_end_cap)
    if live_end < live_start:
        # Nothing has elapsed yet since live_season_start (it's in the
        # future, or today itself) -- an honest empty state, not a
        # fabricated curve.
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
