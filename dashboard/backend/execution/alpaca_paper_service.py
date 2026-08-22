"""Alpaca paper-trading orchestration + audit logging, driven by one of the
leaderboard registry's deterministic strategies (never an LLM call) instead
of the live path's LLM decision.

Deliberately mirrors ``alpaca_live_service.py``'s risk-gate / two-gate design
(off by default, a dry-run review mode, a per-order USD cap, no shorting) --
paper money carries no real financial risk, but the same discipline still
matters: a runaway rebalance loop can still corrupt the paper portfolio's
state or burn through the account's API rate limit. Kept as a fully separate
module and env-var namespace from the live path on purpose, matching this
repo's established rule that paper and live credentials/config must never be
able to cross.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from dashboard.backend.domain.agents.marketplace import get_marketplace_template
from dashboard.backend.domain.leaderboard.strategies import get_strategy
from dashboard.backend.domain.leaderboard.strategies._signal_engine import DailyHistory
from dashboard.backend.execution.alpaca_live_service import risk_gate_orders
from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient
from dashboard.backend.infrastructure.llm.backtest_harness import (
    default_model_name,
    extract_response_text,
    make_llm_client,
    parse_llm_response,
    request_trading_decision,
)
from dashboard.backend.infrastructure.llm.validator import DJIA_30
from dashboard.backend.paths import REPO_ROOT

#: Marketplace templates whose pipeline is a single free-form instruction --
#: directly usable as the LLM prompt for a paper-trading decision cycle.
#: `pipeline-analyst` (3-stage facts->signal->execution) and `ai-hedge-fund`
#: (a separate hosted-analyst-panel runtime, not a prompt at all) are NOT
#: supported by this generic path -- wiring either up would need its own,
#: differently-shaped orchestration, not attempted here. The 2 China A-share
#: templates are excluded too: Alpaca carries no A-share tickers to trade.
SUPPORTED_MARKETPLACE_TEMPLATES = {
    "balanced-starter", "momentum-scout", "blue-chip-steady", "even-split-dow",
    "contrarian-dip-buyer", "sector-rotator", "volatility-guard",
}

logger = logging.getLogger(__name__)

AUDIT_DIR = REPO_ROOT / "dashboard" / "storage" / "audit" / "alpaca_paper"

#: Separate namespace from ALPACA_MAX_ORDER_USD/ALPACA_LIVE_EXECUTE on purpose
#: -- paper and live must never be able to share a kill switch or a cap.
DEFAULT_MAX_ORDER_USD = 1000.0
MIN_ORDER_QUANTITY = 0.0001
_LOOKBACK_CALENDAR_DAYS = 420  # enough for the longest lookback (SMA-200) plus slack

_run_lock = asyncio.Lock()


def execute_enabled() -> bool:
    """True when paper order placement is armed (read fresh on every call)."""
    return os.getenv("ALPACA_PAPER_EXECUTE", "false").strip().lower() in {"1", "true", "yes", "on"}


def max_order_usd() -> float:
    raw = os.getenv("ALPACA_PAPER_MAX_ORDER_USD")
    if raw is None or not str(raw).strip():
        return DEFAULT_MAX_ORDER_USD
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_ORDER_USD
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_MAX_ORDER_USD
    return value


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _audit_sync(event: str, payload: Dict[str, Any]) -> None:
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        record = {"ts": _utcnow_iso(), "event": event, **payload}
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = AUDIT_DIR / f"audit_{day}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        logger.exception("Alpaca paper audit write failed for event %s", event)


async def _audit(event: str, payload: Dict[str, Any]) -> None:
    await asyncio.to_thread(_audit_sync, event, payload)


def fetch_daily_history(client: AlpacaPaperTradingClient, symbols: List[str]) -> DailyHistory:
    """Fetch enough daily bars to cover every strategy's lookback, clamped to
    end = now - 1 day (Alpaca's Basic data plan rejects a query whose `end`
    falls inside the last ~15 minutes; a full calendar day of slack is used
    here since this only needs to run once per day, not intraday)."""
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=_LOOKBACK_CALENDAR_DAYS)
    data_client = StockHistoricalDataClient(client.api_key, client.secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=symbols, timeframe=TimeFrame.Day, start=start, end=end,
        feed=DataFeed.SIP, adjustment="all",
    )
    bars = data_client.get_stock_bars(request)
    df = bars.df
    if df.empty:
        empty = pd.DataFrame()
        return DailyHistory(close=empty, open=empty, high=empty, low=empty, volume=empty)

    def _field(name: str) -> pd.DataFrame:
        cols = {}
        for sym, sub in df.groupby(level=0):
            sub = sub.droplevel(0)
            cols[sym] = sub[name]
        frame = pd.DataFrame(cols).sort_index()
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        return frame

    return DailyHistory(
        close=_field("close"), open=_field("open"), high=_field("high"),
        low=_field("low"), volume=_field("volume"),
    )


def compute_rebalance_orders(
    target_weights: Dict[str, float],
    portfolio_value: float,
    holdings: Dict[str, float],
    prices: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Diff a strategy's target weights against actual current paper holdings
    to produce the buy/sell orders needed to close the gap. Pure function --
    no broker calls. A symbol held but absent from `target_weights` is fully
    liquidated (target weight 0)."""
    orders: List[Dict[str, Any]] = []
    universe = set(target_weights) | set(holdings)
    for symbol in sorted(universe):
        price = prices.get(symbol)
        if not price or price <= 0:
            continue  # no_quote is caught by risk_gate_orders downstream
        target_qty = (target_weights.get(symbol, 0.0) * portfolio_value) / price
        current_qty = float(holdings.get(symbol, 0) or 0)
        delta = target_qty - current_qty
        if abs(delta * price) < 1.0:  # sub-$1 rebalance isn't worth a round trip
            continue
        side = "buy" if delta > 0 else "sell"
        orders.append({"symbol": symbol, "side": side, "quantity": abs(delta)})
    return orders


def _actions_to_orders(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Mirrors ``alpaca_live_service._actions_to_orders`` -- kept as a small,
    intentional duplicate rather than importing a private (underscore-named)
    helper across modules."""
    orders: List[Dict[str, Any]] = []
    for action in actions:
        act = str(action.get("action") or "").lower()
        symbol = str(action.get("symbol") or "").upper()
        shares = action.get("shares")
        if shares is None:
            shares = action.get("position_size")
        if act == "hold" or not symbol or act not in {"buy", "sell"}:
            continue
        try:
            qty = float(shares)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        orders.append({"symbol": symbol, "side": act, "quantity": qty})
    return orders


async def _llm_decision(
    *, model_name: Optional[str], instruction: str, portfolio: Dict[str, Any], prices: Dict[str, float]
) -> Dict[str, Any]:
    """Ask the configured model for actions, given a marketplace template's
    trading instruction as the strategy prompt. Mirrors
    ``alpaca_live_service._llm_decision`` -- raises ``ValueError("llm_unavailable")``
    rather than silently falling back to holds, which would be
    indistinguishable from a genuine all-hold decision."""
    client = make_llm_client()
    if client is None:
        raise ValueError("llm_unavailable")
    model = model_name or default_model_name()

    user_payload = {
        "instruction": instruction,
        "portfolio": portfolio,
        "prices": prices,
        "allowed_symbols": list(DJIA_30),
    }

    def _call() -> Dict[str, Any]:
        response = request_trading_decision(client, prompt=json.dumps(user_payload, default=str), model=model)
        text = extract_response_text(response)
        parsed = parse_llm_response(text)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("actions"), list):
            raise ValueError("llm_unavailable")
        return parsed

    return await asyncio.to_thread(_call)


async def run_paper_for_marketplace_agent(
    *,
    template_id: str,
    model_name: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Run one paper-trading decision cycle for an LLM-driven Marketplace
    template (see ``SUPPORTED_MARKETPLACE_TEMPLATES``). Same two-gate
    dry_run/ALPACA_PAPER_EXECUTE pattern as ``run_paper_for_strategy``, but the
    decision comes from a real LLM call using the template's own trading
    instruction as the prompt -- unlike the deterministic strategies, this
    costs a small amount of real API spend per cycle, not just fake trades."""
    if template_id not in SUPPORTED_MARKETPLACE_TEMPLATES:
        raise ValueError(
            f"marketplace template '{template_id}' is not supported for live/paper "
            f"execution. Supported: {sorted(SUPPORTED_MARKETPLACE_TEMPLATES)}"
        )
    template = get_marketplace_template(template_id)
    if not template:
        raise ValueError(f"marketplace template '{template_id}' not found")
    pipeline = template.get("pipeline") or []
    if not pipeline:
        raise ValueError(f"marketplace template '{template_id}' has no pipeline/instruction")
    instruction = pipeline[0].get("prompt") or ""

    if _run_lock.locked():
        raise ValueError("paper_run_in_progress")
    async with _run_lock:
        return await _execute_paper_llm_run(
            template_id=template_id, instruction=instruction, model_name=model_name,
            symbols=symbols, dry_run=dry_run,
        )


async def _execute_paper_llm_run(
    *, template_id: str, instruction: str, model_name: Optional[str], symbols: Optional[List[str]], dry_run: bool
) -> Dict[str, Any]:
    run_id = f"alpaca_paper_{template_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    client = AlpacaPaperTradingClient()
    account = await asyncio.to_thread(client.get_account)
    if account is None:
        raise ValueError("alpaca_paper_account_unavailable")
    holdings = await asyncio.to_thread(client.get_positions_qty_map)

    universe = sorted(set(symbols or DJIA_30) | set(holdings.keys()))
    prices = await asyncio.to_thread(client.get_quotes, universe)
    portfolio = {"cash": account["cash"], "buying_power": account["buying_power"], "holdings": holdings}

    await _audit(
        "context_snapshot",
        {"run_id": run_id, "template_id": template_id, "portfolio": portfolio, "prices": prices, "universe": universe},
    )

    try:
        llm_result = await _llm_decision(
            model_name=model_name, instruction=instruction, portfolio=portfolio, prices=prices
        )
    except Exception as exc:
        await _audit("decision_failed", {"run_id": run_id, "template_id": template_id, "error": str(exc)[:300]})
        raise

    actions = llm_result.get("actions") or []
    await _audit("decision", {"run_id": run_id, "template_id": template_id, "actions": actions})

    cap_usd = max_order_usd()
    orders, rejections = risk_gate_orders(_actions_to_orders(actions), prices, holdings, cap_usd)
    if rejections:
        await _audit("orders_rejected", {"run_id": run_id, "max_order_usd": cap_usd, "rejections": rejections})

    executions: List[Dict[str, Any]] = []
    should_execute = execute_enabled() and not dry_run

    for order in orders:
        if not should_execute:
            executions.append({"order": order, "status": "skipped", "reason": "dry_run_or_execute_disabled"})
            continue
        try:
            result = await asyncio.to_thread(
                client.submit_market_order, order["symbol"], order["quantity"], order["side"]
            )
            status = (result or {}).get("status", "submitted") if result else "failed"
            executions.append({"order": order, "status": status, "result": result})
            await _audit("order_placed", {"run_id": run_id, "order": order, "result": result})
        except Exception as exc:
            detail = str(exc)[:300]
            logger.exception("Alpaca paper order placement failed for %s", order.get("symbol"))
            executions.append({"order": order, "status": "failed", "reason": "place_order_failed", "error": detail})
            await _audit("order_failed", {"run_id": run_id, "order": order, "error": detail})

    return {
        "run_id": run_id,
        "status": "completed",
        "template_id": template_id,
        "dry_run": dry_run or not execute_enabled(),
        "execute_enabled": execute_enabled(),
        "max_order_usd": cap_usd,
        "account": account,
        "decision": {"actions": actions},
        "orders_reviewed": orders,
        "rejected_orders": rejections,
        "executions": executions,
    }


async def run_paper_for_strategy(
    *,
    strategy_key: str,
    symbols: Optional[List[str]] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Run one paper-trading decision cycle for a registered leaderboard
    strategy. ``dry_run=True`` (the default) always forces review-only, even
    if ``ALPACA_PAPER_EXECUTE`` is set. Real (paper) orders require both
    ``dry_run=False`` on the call and ``ALPACA_PAPER_EXECUTE=true`` in the
    environment -- same two-gate pattern as the live path."""
    if _run_lock.locked():
        raise ValueError("paper_run_in_progress")
    async with _run_lock:
        return await _execute_paper_run(strategy_key=strategy_key, symbols=symbols, dry_run=dry_run)


async def _execute_paper_run(
    *, strategy_key: str, symbols: Optional[List[str]], dry_run: bool
) -> Dict[str, Any]:
    run_id = f"alpaca_paper_{strategy_key}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    strategy = get_strategy({"strategy": strategy_key, "symbols": symbols})
    if not hasattr(strategy, "decide"):
        raise ValueError(f"strategy '{strategy_key}' has no live-trading decide() method")

    client = AlpacaPaperTradingClient()
    account = await asyncio.to_thread(client.get_account)
    if account is None:
        raise ValueError("alpaca_paper_account_unavailable")
    holdings = await asyncio.to_thread(client.get_positions_qty_map)

    # Decision universe is strictly the strategy's OWN symbols -- never the
    # account's existing holdings. This account (like any real paper account
    # after enough testing) can be carrying unrelated positions from earlier,
    # separate activity; letting those leak into e.g. a momentum ranking
    # would silently substitute "whatever this account happens to already
    # hold" for the strategy's actual intended universe.
    strategy_symbols = strategy.required_symbols()

    history = await asyncio.to_thread(fetch_daily_history, client, strategy_symbols)
    await _audit(
        "context_snapshot",
        {"run_id": run_id, "account": account, "holdings": holdings, "strategy_universe": strategy_symbols},
    )

    try:
        target_weights = strategy.decide(history)
    except Exception as exc:
        await _audit("decision_failed", {"run_id": run_id, "error": str(exc)[:300]})
        raise

    if target_weights is None:
        await _audit("no_action", {"run_id": run_id, "reason": "strategy_returned_none"})
        return {
            "run_id": run_id, "status": "completed", "action": "none",
            "reason": "strategy decided to make no trades this cycle",
            "account": account,
        }

    await _audit("decision", {"run_id": run_id, "target_weights": target_weights})

    price_symbols = sorted(set(target_weights) | set(holdings))
    prices = await asyncio.to_thread(client.get_quotes, price_symbols)
    portfolio_value = float(account["cash"]) + sum(
        holdings.get(sym, 0) * prices.get(sym, 0) for sym in holdings
    )

    raw_orders = compute_rebalance_orders(target_weights, portfolio_value, holdings, prices)
    cap_usd = max_order_usd()
    orders, rejections = risk_gate_orders(raw_orders, prices, holdings, cap_usd)
    if rejections:
        await _audit("orders_rejected", {"run_id": run_id, "max_order_usd": cap_usd, "rejections": rejections})

    executions: List[Dict[str, Any]] = []
    should_execute = execute_enabled() and not dry_run

    for order in orders:
        if not should_execute:
            executions.append({"order": order, "status": "skipped", "reason": "dry_run_or_execute_disabled"})
            continue
        try:
            result = await asyncio.to_thread(
                client.submit_market_order, order["symbol"], order["quantity"], order["side"]
            )
            status = (result or {}).get("status", "submitted") if result else "failed"
            executions.append({"order": order, "status": status, "result": result})
            await _audit("order_placed", {"run_id": run_id, "order": order, "result": result})
        except Exception as exc:
            detail = str(exc)[:300]
            logger.exception("Alpaca paper order placement failed for %s", order.get("symbol"))
            executions.append({"order": order, "status": "failed", "reason": "place_order_failed", "error": detail})
            await _audit("order_failed", {"run_id": run_id, "order": order, "error": detail})

    return {
        "run_id": run_id,
        "status": "completed",
        "dry_run": dry_run or not execute_enabled(),
        "execute_enabled": execute_enabled(),
        "max_order_usd": cap_usd,
        "account": account,
        "target_weights": target_weights,
        "portfolio_value": round(portfolio_value, 2),
        "orders_reviewed": orders,
        "rejected_orders": rejections,
        "executions": executions,
    }
