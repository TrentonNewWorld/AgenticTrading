"""Promotes a 'ready' Testing-page result into the real Strategy Catalog.

Writes to the same ``leaderboard.json`` roster the Strategy Catalog and
Competition Leaderboard both read (see ``domain.leaderboard.catalog``'s
module docstring for why that file is the single source of truth) -- so
"Add to strategies list" makes the strategy show up everywhere a catalog
entry does, immediately, with no separate registration step.
"""

from __future__ import annotations

from typing import Any, Dict

from dashboard.backend.domain.leaderboard.catalog import (
    _save_leaderboard_config,
    _slugify,
    _unique_strategy_id,
    append_placeholder_to_cache,
    load_leaderboard_config,
)

EXPORT_FORMAT = "newworldtrading-strategy-v1"


def add_to_catalog(*, name: str, description: str, code: str) -> Dict[str, Any]:
    config = load_leaderboard_config()
    strategy_id = _unique_strategy_id(_slugify(name), config)
    entry: Dict[str, Any] = {
        "id": strategy_id,
        "name": name,
        "label": "Baseline Strategy",
        "model": name,
        "strategy": "sandboxed",
        "code": code,
        "symbols": [],
        "auto_compute": False,
        "source": "Tested Upload",
        "description": description or f'Uploaded strategy "{name}", scanned and backtested over the most '
                                       "recent year before being added.",
    }
    config.setdefault("strategies", []).append(entry)
    _save_leaderboard_config(config)
    append_placeholder_to_cache(
        key=strategy_id, name=name, source=entry["source"], description=entry["description"],
    )
    return entry


def build_export_package(*, name: str, description: str, code: str, source: str) -> Dict[str, Any]:
    from datetime import datetime, timezone

    return {
        "format": EXPORT_FORMAT,
        "name": name,
        "description": description or "",
        "code": code,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": source,
    }


REFERENCE_FORMAT_SUFFIX = "-strategy-reference-v1"


def install_reference_package(pkg: Dict[str, Any]) -> Dict[str, Any]:
    """Registers a built-in-strategy reference export into THIS bot's catalog.

    The sellable strategy packs export registry strategies as reference
    packages (the Python class ships with every bot; only the roster entry
    travels). Without this, a blank-slate install had no way to load them --
    the Testing page rejected every file in a purchased pack (found testing
    the buyer flow, 2026-08-30)."""
    entry_src = pkg.get("catalog_entry")
    if not isinstance(entry_src, dict) or not entry_src.get("strategy"):
        raise ValueError(
            "this reference file carries no installable catalog entry -- "
            "re-export it from a current bot (Strategy page -> Export)"
        )

    from dashboard.backend.domain.leaderboard.strategies import get_strategy

    probe_config = dict(entry_src)
    strat = get_strategy(probe_config)  # raises on unknown strategy type
    if not hasattr(strat, "decide"):
        raise ValueError(
            f"strategy type '{entry_src.get('strategy')}' has no decide() -- "
            "it cannot be run from the catalog"
        )

    config = load_leaderboard_config()
    display_name = pkg.get("name") or entry_src.get("model") or entry_src.get("id")
    wanted_id = entry_src.get("id") or _slugify(display_name)
    taken = {s.get("id") for s in config.get("strategies", [])}
    strategy_id = wanted_id if wanted_id not in taken else _unique_strategy_id(_slugify(display_name), config)

    entry = dict(entry_src)
    entry["id"] = strategy_id
    entry.setdefault("label", "Baseline Strategy")
    entry["model"] = display_name
    entry.setdefault("name", "NewWorldTrading")
    entry["source"] = "Installed Pack"
    if pkg.get("description"):
        entry["description"] = pkg["description"]
    config.setdefault("strategies", []).append(entry)
    _save_leaderboard_config(config)
    append_placeholder_to_cache(
        key=strategy_id, name=display_name, source=entry["source"],
        description=entry.get("description", ""),
    )

    values = pkg.get("parameter_values") or {}
    schema = pkg.get("parameter_schema") or {}
    if values and schema:
        from dashboard.backend.domain.leaderboard.strategy_overrides import set_overrides
        try:
            set_overrides(strategy_id, schema, values)
        except Exception:  # noqa: BLE001 -- params are a nicety; the install must not fail on them
            pass
    return entry
