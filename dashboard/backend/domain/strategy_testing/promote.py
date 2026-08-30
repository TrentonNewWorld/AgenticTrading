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

EXPORT_FORMAT = "agentic-trading-lab-strategy-v1"


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
