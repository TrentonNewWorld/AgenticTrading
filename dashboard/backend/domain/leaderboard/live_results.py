"""Actual live-account trading results for the Overview's Live Trading
Leaderboard -- built from orders that were really placed, priced by the
broker's own numbers.

This deliberately replaces the *notional* ledger in ``real_trading.py`` as
the data behind ``GET /api/v1/leaderboard/real-trading``. That ledger records
what a strategy's target weights *would* be worth against its allocated
capital, and it writes a snapshot on every run -- including dry-run reviews
that placed nothing -- so its board showed flat $1,000 rows for strategies
that never traded, and a return number no real dollar ever experienced. The
operator asked for the opposite: the board must show what actually happened
to real money. (``real_trading.py`` itself stays: the catalog page still uses
its allocation/per-stock-amount stores, and the snapshot writer still feeds
its own tables.)

Two sources of truth, combined:

* **What was traded, per strategy** -- the ``order_placed`` events in the
  live audit trail (``storage/audit/alpaca_live/audit_*.jsonl``). That log is
  written at the moment an order is accepted by Alpaca and is the only record
  in this codebase that attributes an order to the strategy that produced it
  (the shared account itself cannot -- see ``real_trading.py``'s docstring).
  Skipped/dry-run/failed orders never produce this event, so a strategy that
  never traded simply has no entry here.
* **What it is worth now, and the account curve** -- the broker: current
  positions with Alpaca's own ``avg_entry_price``/``unrealized_pl``, account
  equity/cash, and the account's portfolio-history equity series.

Cost basis per strategy uses the audit event's ``notional_usd`` (the order's
value at quote time). Fills can differ by a spread's width; the account
section's P&L uses Alpaca's fill-derived numbers, so any such gap is visible
rather than hidden. An order placed outside market hours is still counted --
it is queued at the broker -- and rows whose broker position hasn't caught up
to the ledger quantity are flagged ``pending_fill`` instead of being dropped.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dashboard.backend.paths import REPO_ROOT

AUDIT_DIR = REPO_ROOT / "dashboard" / "storage" / "audit" / "alpaca_live"

#: run_id shapes written by execution/alpaca_live_service.py:
#:   alpaca_live_<strategy_key>_YYYYmmdd_HHMMSS_<8 hex>   (catalog strategy)
#:   alpaca_live_YYYYmmdd_HHMMSS_<8 hex>                  (LLM agent run)
_RUN_ID_SUFFIX = re.compile(r"_(\d{8})_(\d{6})_[0-9a-f]{8}$")
_LIVE_PREFIX = "alpaca_live_"

#: Broker snapshot cache. The board refetches on every tab click and the
#: payload costs three broker round-trips; a short TTL keeps clicks cheap
#: without the numbers ever being meaningfully stale.
_CACHE_TTL_SECONDS = 30.0
_cache: Dict[str, Any] = {"at": 0.0, "payload": None}


def _strategy_key_from_run_id(run_id: str) -> str:
    if not run_id.startswith(_LIVE_PREFIX):
        return "unknown"
    body = _RUN_ID_SUFFIX.sub("", run_id[len(_LIVE_PREFIX):])
    return body or "llm_agent"


def _iter_order_events() -> List[Dict[str, Any]]:
    """Every ``order_placed`` event across the whole live audit history, in
    file/line order (files are day-stamped and append-only, so that is
    chronological)."""
    events: List[Dict[str, Any]] = []
    if not AUDIT_DIR.exists():
        return events
    for path in sorted(AUDIT_DIR.glob("audit_*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("event") != "order_placed":
                        continue
                    order = record.get("order") or {}
                    symbol = str(order.get("symbol") or "").upper()
                    if not symbol:
                        continue
                    events.append(
                        {
                            "ts": record.get("ts"),
                            "strategy_key": _strategy_key_from_run_id(str(record.get("run_id") or "")),
                            "symbol": symbol,
                            "side": str(order.get("side") or "").lower(),
                            "qty": float(order.get("quantity") or 0.0),
                            "notional_usd": float(order.get("notional_usd") or 0.0),
                        }
                    )
        except OSError:
            continue
    return events


def _build_strategy_ledgers(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Average-cost position ledger per strategy from its executed orders."""
    ledgers: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        led = ledgers.setdefault(
            ev["strategy_key"],
            {"positions": {}, "n_orders": 0, "first_trade": ev["ts"], "last_trade": ev["ts"], "realized_pl": 0.0},
        )
        led["n_orders"] += 1
        led["last_trade"] = ev["ts"]
        pos = led["positions"].setdefault(ev["symbol"], {"qty": 0.0, "cost": 0.0})
        if ev["side"] == "buy":
            pos["qty"] += ev["qty"]
            pos["cost"] += ev["notional_usd"]
        elif ev["side"] == "sell" and pos["qty"] > 0:
            sold = min(ev["qty"], pos["qty"])
            avg_cost = pos["cost"] / pos["qty"] if pos["qty"] else 0.0
            led["realized_pl"] += ev["notional_usd"] - avg_cost * sold
            pos["cost"] -= avg_cost * sold
            pos["qty"] -= sold
    return ledgers


def _broker_snapshot() -> Dict[str, Any]:
    """Account, positions and equity history from the live broker. Missing
    credentials or a broker outage degrade to an ``account: None`` payload --
    the per-strategy order history still renders without it."""
    try:
        from dashboard.backend.infrastructure.brokers.alpaca_live import AlpacaLiveTradingClient

        client = AlpacaLiveTradingClient()
        account = client.get_account()
        positions = client.get_positions_detailed()
        history = client.get_portfolio_history(period="1M", timeframe="1D")
        return {"account": account, "positions": positions, "history": history, "error": None}
    except Exception as exc:  # credentials missing, network down, ...
        return {"account": None, "positions": [], "history": {"timestamps": [], "equity": []}, "error": str(exc)[:200]}


def get_live_results(force_refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force_refresh and _cache["payload"] is not None and now - _cache["at"] < _CACHE_TTL_SECONDS:
        return _cache["payload"]

    events = _iter_order_events()
    ledgers = _build_strategy_ledgers(events)
    broker = _broker_snapshot()

    # Broker truth per symbol, for pricing ledger rows and flagging fills.
    by_symbol = {p["symbol"]: p for p in broker["positions"]}

    entries: List[Dict[str, Any]] = []
    for key, led in ledgers.items():
        positions_out: List[Dict[str, Any]] = []
        invested = 0.0
        current_value = 0.0
        for symbol, pos in sorted(led["positions"].items()):
            if pos["qty"] <= 1e-9:
                continue
            broker_pos = by_symbol.get(symbol)
            price = broker_pos.get("current_price") if broker_pos else None
            value = pos["qty"] * price if price else None
            # An accepted order outside market hours sits queued at the
            # broker; the position appears only after the open. Flag rather
            # than drop, so the board never claims money wasn't committed.
            pending = broker_pos is None or float(broker_pos.get("qty") or 0.0) + 1e-6 < pos["qty"]
            invested += pos["cost"]
            if value is not None:
                current_value += value
            elif pending:
                current_value += pos["cost"]  # queued at ~order-time value
            positions_out.append(
                {
                    "symbol": symbol,
                    "qty": round(pos["qty"], 6),
                    "cost_basis": round(pos["cost"], 2),
                    "current_price": price,
                    "market_value": round(value, 2) if value is not None else None,
                    "profit": round(value - pos["cost"], 2) if value is not None else None,
                    "pending_fill": pending,
                }
            )
        profit = current_value - invested + led["realized_pl"]
        entries.append(
            {
                "key": key,
                "invested": round(invested, 2),
                "current_value": round(current_value, 2),
                "profit": round(profit, 2),
                "realized_pl": round(led["realized_pl"], 2),
                "return_pct": round(profit / invested * 100.0, 2) if invested > 0 else 0.0,
                "n_orders": led["n_orders"],
                "first_trade": led["first_trade"],
                "last_trade": led["last_trade"],
                "positions": positions_out,
            }
        )
    entries.sort(key=lambda e: e["profit"], reverse=True)

    payload = {
        "as_of": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "account": broker["account"],
        "account_positions": broker["positions"],
        "account_history": broker["history"],
        "broker_error": broker["error"],
        "entries": entries,
    }
    _cache["at"] = now
    _cache["payload"] = payload
    return payload
