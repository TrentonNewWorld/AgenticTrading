"""Crypto Strategy: every strategy in dashboard/config/
leaderboard_crypto.json, presented as one card each. Mirrors
domain/futures/catalog.py and domain/forex/catalog.py exactly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from dashboard.backend.domain.crypto.strategies import get_strategy
from dashboard.backend.paths import CONFIG_DIR, REPO_ROOT

CACHE_PATH = REPO_ROOT / "dashboard" / "storage" / "data" / "crypto_strategy_catalog_cache.json"
LEADERBOARD_CRYPTO_CONFIG_PATH = CONFIG_DIR / "leaderboard_crypto.json"
CACHE_TTL_HOURS = 24
INITIAL_CAPITAL = 1000.0

EXPORT_FORMAT = "agentic-trading-lab-strategy-v1"

_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "cx_momentum_basket": {"source": "Crypto Strategy",
        "description": "Puts $150 into any major coin (BTC, ETH, SOL, LTC, LINK, DOGE) that closed above its previous close, and closes out on the first down day."},
    "cx_dip_reversion": {"source": "Crypto Strategy",
        "description": "Puts $150 into any major coin that fell 3%+ from its previous close, and closes out once it recovers back above that close."},
}


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    name: str
    source: str
    description: str
    symbols: List[str]


def _description_for(strategy_id: str, model_name: str, strat: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    if strat and strat.get("description"):
        return {"source": strat.get("source") or "Crypto Strategy", "description": strat["description"]}
    return _DESCRIPTIONS.get(strategy_id) or {
        "source": "Crypto Strategy",
        "description": f"{model_name} -- no description written yet.",
    }


def load_leaderboard_config() -> Dict[str, Any]:
    with LEADERBOARD_CRYPTO_CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _save_leaderboard_config(config: Dict[str, Any]) -> None:
    with LEADERBOARD_CRYPTO_CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def remove_strategy(key: str) -> bool:
    config = load_leaderboard_config()
    strategies = config.get("strategies", [])
    kept = [s for s in strategies if s.get("id") != key]
    removed = next((s for s in strategies if s.get("id") == key), None)
    if removed is None:
        return False
    config["strategies"] = kept
    _save_leaderboard_config(config)
    # Evict from the served cache too, not just the roster file -- without
    # this a successful removal stayed on screen until the cache TTL and the
    # Remove button looked broken (found live on the stocks catalog
    # 2026-08-29; all five catalogs shared the bug).
    cached = _load_cache()
    if cached and isinstance(cached.get("entries"), list):
        cached["entries"] = [e for e in cached["entries"] if e.get("key") != key]
        _save_cache(cached)
    return True


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "strategy"


def _unique_strategy_id(base_slug: str, config: Dict[str, Any]) -> str:
    existing = {s.get("id") for s in config.get("strategies", [])}
    candidate = f"cx_{base_slug}" if not base_slug.startswith("cx_") else base_slug
    n = 2
    while candidate in existing:
        candidate = f"cx_{base_slug}_{n}"
        n += 1
    return candidate


def add_to_catalog(*, name: str, description: str, code: str) -> Dict[str, Any]:
    config = load_leaderboard_config()
    strategy_id = _unique_strategy_id(_slugify(name), config)
    entry: Dict[str, Any] = {
        "id": strategy_id,
        "name": name,
        "label": "Baseline Strategy",
        "model": name,
        "strategy": "cx_sandboxed",
        "code": code,
        "symbols": [],
        "source": "Tested Upload",
        "description": description or f'Uploaded crypto strategy "{name}", scanned and backtested over the '
                                       "most recent year before being added.",
    }
    config.setdefault("strategies", []).append(entry)
    _save_leaderboard_config(config)
    _append_placeholder_to_cache(entry)
    return entry


def convert_agent_to_strategy(
    *, name: str, model_id: Optional[str], strategy_prompt: str, symbols: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Write a new Crypto Strategy Catalog entry that trades exactly like the
    given My Agents crypto agent's pipeline instruction, via the
    ``cx_llm_agent`` strategy (its ``strategy_prompt`` is replayed through the
    LLM at every catalog compute). Mirrors
    domain/leaderboard/catalog.py::convert_agent_to_strategy for the stocks
    case -- called from the same agent-conversion route
    (api/routers/agents.py), dispatched there by the agent's asset_class."""
    prompt = (strategy_prompt or "").strip()
    if not prompt:
        raise ValueError("this agent has no trading instruction to convert")

    config = load_leaderboard_config()
    strategy_id = _unique_strategy_id(_slugify(name), config)
    entry: Dict[str, Any] = {
        "id": strategy_id,
        "name": name,
        "label": "My Agent",
        "model": model_id or name,
        "strategy": "cx_llm_agent",
        "strategy_prompt": prompt,
        "symbols": list(symbols) if symbols else [],
        "source": "My Agents",
        "description": f'Converted from the My Agents crypto agent "{name}".',
    }
    config.setdefault("strategies", []).append(entry)
    _save_leaderboard_config(config)
    _append_placeholder_to_cache(entry)
    return entry


def _catalog_roster() -> List[CatalogEntry]:
    config = load_leaderboard_config()
    entries: List[CatalogEntry] = []
    for strat in config.get("strategies", []):
        key = strat.get("id")
        model_name = strat.get("model") or key
        meta = _description_for(key, model_name, strat)
        entries.append(CatalogEntry(
            key=key, name=model_name, source=meta["source"], description=meta["description"],
            symbols=strat.get("symbols") or ["BTC/USD"],
        ))
    return entries


def _config_for(entry: CatalogEntry) -> Dict[str, Any]:
    config = load_leaderboard_config()
    strat = next((s for s in config.get("strategies", []) if s.get("id") == entry.key), None)
    if strat is None:
        return {"id": entry.key, "name": entry.name, "strategy": entry.key, "symbols": entry.symbols}
    base = {k: v for k, v in strat.items() if k not in ("label", "source", "description")}
    base.setdefault("id", entry.key)
    base.setdefault("name", entry.name)
    return base


def _metrics(curve: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not curve:
        return {"final": INITIAL_CAPITAL, "return_pct": 0.0, "max_drawdown_pct": 0.0}
    equity = np.array([row["equity"] for row in curve], dtype=float)
    final = float(equity[-1])
    ret_pct = (final / INITIAL_CAPITAL - 1) * 100
    peak = np.maximum.accumulate(equity)
    dd = float(((equity - peak) / peak).min() * 100) if len(equity) else 0.0
    return {"final": round(final, 2), "return_pct": round(ret_pct, 2), "max_drawdown_pct": round(dd, 2)}


def _current_window() -> tuple:
    from dashboard.backend.domain.leaderboard.baselines import contest_window_for_year

    start, end = contest_window_for_year(datetime.now(timezone.utc).date())
    return start.isoformat(), end.isoformat()


def _compute_all(force_refresh: bool = False) -> Dict[str, Any]:
    start_date, end_date = _current_window()
    entries_out = []
    for entry in _catalog_roster():
        try:
            strat = get_strategy(_config_for(entry))
            curve = strat.run(start_date, end_date, INITIAL_CAPITAL)
            metrics = _metrics(curve)
            metrics["n_trades"] = strat.num_trades()
            sampled = curve[::3] if len(curve) > 120 else curve
            if curve and sampled[-1] is not curve[-1]:
                sampled = sampled + [curve[-1]]
        except Exception as exc:
            metrics = {"final": INITIAL_CAPITAL, "return_pct": 0.0, "max_drawdown_pct": 0.0, "n_trades": 0}
            sampled = []
            metrics["error"] = str(exc)[:200]

        entries_out.append({
            "key": entry.key,
            "name": entry.name,
            "source": entry.source,
            "description": entry.description,
            "metrics": metrics,
            "equity_curve": [{"t": row["date"], "equity": row["equity"]} for row in sampled],
        })

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start_date": start_date, "end_date": end_date},
        "initial_capital": INITIAL_CAPITAL,
        "entries": entries_out,
    }


def generate_report(key: str) -> Dict[str, Any]:
    entry = next((e for e in _catalog_roster() if e.key == key), None)
    if entry is None:
        raise ValueError(f"no strategy '{key}' in the crypto catalog")

    start_date, end_date = _current_window()
    strat = get_strategy(_config_for(entry))
    curve = strat.run(start_date, end_date, INITIAL_CAPITAL)
    if not curve:
        raise RuntimeError("no crypto market data available (Alpaca crypto data fetch failed for this strategy's symbol universe)")

    from dashboard.backend.domain.crypto.report import build_report

    report = build_report(curve, INITIAL_CAPITAL)
    report["key"] = key
    report["name"] = entry.name
    report["window"] = {"start_date": start_date, "end_date": end_date}
    report["n_trades"] = strat.num_trades()
    return report


def build_export(key: str) -> Dict[str, Any]:
    config = load_leaderboard_config()
    strat_entry = next((s for s in config.get("strategies", []) if s.get("id") == key), None)
    if strat_entry is None:
        raise ValueError(f"no strategy '{key}' in the crypto catalog")

    entry = next((e for e in _catalog_roster() if e.key == key), None)
    strat = get_strategy(_config_for(entry)) if entry else None
    code = strat.code() if strat else None
    if not code:
        return {
            "format": "agentic-trading-lab-strategy-reference-v1",
            "executable": False,
            "name": strat_entry.get("name") or key,
            "description": _description_for(key, strat_entry.get("name") or key, strat_entry)["description"],
            "note": "This strategy's source could not be resolved for export.",
        }
    return {
        "format": EXPORT_FORMAT,
        "name": strat_entry.get("name") or key,
        "description": _description_for(key, strat_entry.get("name") or key, strat_entry)["description"],
        "code": code,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "crypto_strategy_catalog",
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


def _append_placeholder_to_cache(entry: Dict[str, Any]) -> None:
    """Make a just-added roster entry visible on the catalog page now, rather
    than after the 24h cache TTL. Mirror image of remove_strategy's cache
    eviction (same 2026-08-29 audit): without it "Add to Strategy" looked
    broken for a day. The real curve/metrics fill in on the next recompute."""
    cached = _load_cache()
    if not cached or not isinstance(cached.get("entries"), list):
        return
    key = entry["id"]
    if any(e.get("key") == key for e in cached["entries"]):
        return
    cached["entries"].append({
        "key": key,
        "name": entry["name"],
        "source": entry.get("source", ""),
        "description": entry.get("description", ""),
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
    if not force_refresh:
        cached = _load_cache()
        if cached and not _is_stale(cached):
            return cached

    payload = _compute_all(force_refresh=force_refresh)
    if payload.get("entries"):
        _save_cache(payload)
        return payload

    cached = _load_cache()
    return cached or payload
