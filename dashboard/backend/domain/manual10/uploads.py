"""Uploaded strategies: submission, review, and the interval-based tick that
runs an approved one. See sandbox.py for the actual execution security
boundary -- this module owns the *workflow* around it (submit -> validate ->
LLM risk review -> pending -> human approval -> selectable/activatable).

Deliberately paper-only: an uploaded strategy never auto-promotes to real
money the way the built-in Top 10 screener does (see engine.py). A human can
still manually promote one of its paper positions to real via the same
manual_promote button every other position has -- but nothing here decides
on its own to spend real money on code a human hasn't fully vetted, no
matter how it scored on review.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dashboard.backend.domain.manual10 import repository as repo
from dashboard.backend.domain.manual10.sandbox import StrategyCodeError, run_decide, validate_code
from dashboard.backend.execution._alpaca_strategy_shared import compute_rebalance_orders, fetch_daily_history
from dashboard.backend.infrastructure.llm.backtest_harness import (
    HAS_ANTHROPIC, extract_response_text, make_llm_client,
)
from dashboard.backend.infrastructure.llm.validator import DJIA_30

#: Paper-only nominal capital an uploaded strategy rebalances within -- not
#: tied to any real account balance (see the module docstring: these never
#: touch real money automatically).
INITIAL_CAPITAL = 1000.0

MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 240

_RISK_REVIEW_SYSTEM_PROMPT = """You are a security reviewer for a Python trading strategy upload feature.
The code you review already passed a strict static-analysis allowlist (only math/statistics/json/datetime imports,
no eval/exec/open/os/subprocess/socket, no dunder attribute access), and will run in an isolated subprocess with
no credentials, no network, and a hard timeout. Your job is a SECOND, independent look for anything suspicious
that a mechanical check might miss: obfuscated intent, unusual control flow that looks designed to evade review,
or logic that doesn't look like a real trading strategy at all. Respond with a short plain-text assessment (2-4
sentences) and end with exactly one line: "RISK: low" or "RISK: medium" or "RISK: high"."""


class UploadError(ValueError):
    """A strategy upload that can't be accepted as submitted -- the API layer
    maps this straight to a 400 with the specific reason."""


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "strategy"


def _unique_key(base_slug: str) -> str:
    existing = repo.list_all_strategy_keys()
    candidate = f"uploaded_{base_slug}"
    n = 2
    while candidate in existing:
        candidate = f"uploaded_{base_slug}_{n}"
        n += 1
    return candidate


def _llm_risk_review(code: str, user_id: Optional[int] = None) -> str:
    """A heuristic second opinion on top of the real sandbox boundary -- see
    sandbox.py's module docstring for why this is not itself the security
    control. Never blocks an upload on its own (a human always makes the
    approve/reject call, seeing this note); if no LLM is configured, says so
    plainly rather than pretending a review happened.

    ``user_id``, when given, prefers that signed-in user's own
    Connections-saved key over the server's env-var key -- see
    infrastructure/llm/providers/__init__.py's make_llm_client docstring."""
    if not HAS_ANTHROPIC:
        return "No LLM available in this environment -- risk review was not performed. RISK: unknown"
    client = make_llm_client(user_id=user_id)
    if client is None:
        return "No LLM API key configured -- risk review was not performed. RISK: unknown"
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=_RISK_REVIEW_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Review this uploaded strategy code:\n\n```python\n{code}\n```"}],
        )
        return extract_response_text(response).strip()
    except Exception as exc:
        return f"Risk review call failed: {exc}. RISK: unknown"


def submit_upload(
    *, name: str, description: str, code: str, interval_minutes: int, user_id: Optional[int] = None,
) -> Dict[str, Any]:
    if not name or not name.strip():
        raise UploadError("name is required")
    if not (MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES):
        raise UploadError(f"interval_minutes must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}")

    try:
        validate_code(code)
    except StrategyCodeError as exc:
        raise UploadError(f"code rejected by static analysis: {exc}") from exc

    review_notes = _llm_risk_review(code, user_id=user_id)
    key = _unique_key(_slugify(name))
    return repo.create_uploaded_strategy(
        key=key, name=name.strip(), description=description or "", code=code,
        interval_minutes=interval_minutes, review_status="pending", review_notes=review_notes,
    )


def approve_upload(key: str) -> Dict[str, Any]:
    strategy = repo.get_strategy_def(key)
    if strategy is None or strategy["kind"] != "uploaded":
        raise UploadError(f"no uploaded strategy '{key}'")
    _set_review_status(key, "approved")
    return repo.get_strategy_def(key)


def reject_upload(key: str) -> Dict[str, Any]:
    strategy = repo.get_strategy_def(key)
    if strategy is None or strategy["kind"] != "uploaded":
        raise UploadError(f"no uploaded strategy '{key}'")
    _set_review_status(key, "rejected")
    return repo.get_strategy_def(key)


def _set_review_status(key: str, status: str) -> None:
    import sqlite3
    conn = sqlite3.connect(str(repo.DB_PATH))
    try:
        conn.execute("UPDATE manual10_strategies SET review_status = ? WHERE key = ?", (status, key))
        conn.commit()
    finally:
        conn.close()


def _price_history_json(history, symbols: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """DailyHistory (pandas, from fetch_daily_history) -> the plain
    JSON-serializable shape an uploaded strategy's decide() receives -- never
    hand a sandboxed process a pandas object."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    if history.close.empty:
        return out
    for symbol in symbols:
        if symbol not in history.close.columns:
            continue
        series = history.close[symbol].dropna()
        out[symbol] = [{"t": str(idx.date()), "close": float(v)} for idx, v in series.items()]
    return out


def tick_uploaded_strategy(trading_date: str, strategy_key: str, strategy_def: Dict[str, Any], session) -> Dict[str, Any]:
    """Run this uploaded strategy's decide() on its own fixed interval and
    rebalance its paper positions to match -- no opening-range screener, no
    promotion window, no real-money path (see module docstring)."""
    if strategy_def["review_status"] != "approved":
        return {"phase": "not_approved"}

    day = repo.ensure_day(trading_date, strategy_key)
    last_run = day.get("screener_completed_at")  # reused as "last tick" timestamp for uploaded strategies
    interval = timedelta(minutes=strategy_def["interval_minutes"] or MIN_INTERVAL_MINUTES)
    if last_run:
        last_run_dt = datetime.fromisoformat(last_run)
        if session.now < last_run_dt + interval:
            return {"phase": "waiting"}

    from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient
    client = AlpacaPaperTradingClient()

    try:
        history = fetch_daily_history(client, DJIA_30)
    except Exception as exc:
        print(f"manual10: uploaded strategy {strategy_key} history fetch failed: {exc}")
        return {"phase": "error"}

    price_history = _price_history_json(history, DJIA_30)
    weights = run_decide(strategy_def["code"], price_history, timeout=10)

    open_positions = repo.list_positions(trading_date, strategy_key, bucket="paper", status="open")
    holdings = {p["symbol"]: p["shares"] for p in open_positions}
    all_symbols = sorted(set(weights) | set(holdings))
    prices = client.get_quotes(all_symbols) if all_symbols else {}
    for symbol, price in prices.items():
        repo.record_price_snapshot(trading_date, strategy_key, symbol, price, ts=session.now.isoformat())

    # Mark-to-market: cash (capital minus what's actually invested at cost)
    # plus current value of held positions.
    position_value = sum(holdings[s] * prices.get(s, 0) for s in holdings)
    cost_basis = sum(p["shares"] * p["entry_price"] for p in open_positions)
    cash = INITIAL_CAPITAL - cost_basis
    portfolio_value = cash + position_value

    orders = compute_rebalance_orders(weights, portfolio_value, holdings, prices)
    execute = os.getenv("ALPACA_PAPER_EXECUTE", "false").strip().lower() in {"1", "true", "yes", "on"}

    for order in orders:
        symbol = order["symbol"]
        qty = order["quantity"]
        price = prices.get(symbol)
        if not price:
            continue
        if order["side"] == "sell":
            matching = next((p for p in open_positions if p["symbol"] == symbol), None)
            if matching:
                if execute:
                    try:
                        client.submit_market_order(symbol, matching["shares"], "sell")
                    except Exception as exc:
                        print(f"manual10: uploaded strategy {strategy_key} sell failed for {symbol}: {exc}")
                        continue
                repo.close_position(matching["id"], exit_price=price, close_reason="rebalance")
        else:
            if execute:
                order_result = client.submit_market_order(symbol, qty, "buy")
                if order_result is None:
                    continue
            repo.open_position(
                trading_date=trading_date, strategy_key=strategy_key, symbol=symbol, bucket="paper",
                shares=qty, entry_price=price,
            )

    repo.update_day(trading_date, strategy_key, phase="holding", screener_completed_at=session.now.isoformat())
    return {"phase": "holding", "orders": len(orders)}
