"""Day-by-day event-driven backtest for uploaded futures strategies, and for
My Agents' LLM-driven futures agents.

Structurally similar to domain/options/backtester.py's day loop (see that
module's docstring for the shared reasoning), but considerably simpler:
futures positions are single-instrument with no expiration/settlement to
model at all -- a position just gets marked to market every day until the
strategy itself decides to close it. No contract enumeration either, since
Yahoo Finance's continuous-contract symbols (infrastructure/market_data/
yahoo_futures.py) are used directly with no per-expiration-month chain to
reconstruct.

Both entry points below (``run_backtest`` for a sandboxed-code Manual/Testing
strategy, ``run_llm_agent_backtest`` for a My Agents pipeline) share the same
day-by-day cash/position loop (``_run_daybyday``) and differ only in how a
day's order intents are decided -- a code sandbox call vs an LLM call. Both
decision paths converge on ``_clean_intents`` (domain/futures/sandbox.py) for
the same reason: an LLM's JSON response needs exactly the same untrusted-input
validation a sandboxed script's stdout does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Any, Callable, Dict, List, Optional

from dashboard.backend.domain.futures.sandbox import _clean_intents, run_decide_futures
from dashboard.backend.infrastructure.market_data.yahoo_futures import (
    MarketDataUnavailableError,
    get_futures_daily_bars,
)

DecideFn = Callable[[str, List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Any]], List[Dict[str, Any]]]

#: Trailing daily closes carried in quotes[sym]["closes"] (see _run_daybyday).
#: 60 covers the longest indicator lookback among registered strategies
#: (50-day SMA) with slack. The live engine mirrors this depth.
CLOSES_LOOKBACK = 60

_LLM_AGENT_SYSTEM_PROMPT = """You are trading futures in a paper backtest for an agent with a fixed \
strategy instruction. At each step you receive today's date, currently-held positions, current \
quotes (price and yesterday's close per symbol), and account cash/equity. Follow the strategy \
instruction below exactly. Respond with ONLY a JSON array of order intents -- no prose, no markdown \
fences: [{{"action": "open"|"close", "symbol": "...", "side": "buy"|"sell", "qty": integer}}, ...]. \
qty is a positive whole number of contracts. Return [] to do nothing this step.

Strategy instruction:
{prompt}"""


@dataclass
class _OpenPosition:
    symbol: str
    side: str  # "buy" (long) | "sell" (short)
    qty: int
    entry_price: float


def _mark_to_market(positions: List[_OpenPosition], prices: Dict[str, float]) -> float:
    total = 0.0
    for pos in positions:
        price = prices.get(pos.symbol, pos.entry_price)
        value = pos.qty * price
        total += value if pos.side == "buy" else -value
    return total


def _positions_payload(positions: List[_OpenPosition]) -> List[Dict[str, Any]]:
    return [{"symbol": p.symbol, "qty": p.qty} for p in positions]


def _run_daybyday(
    decide: DecideFn,
    symbols: List[str],
    start: date_cls,
    end: date_cls,
    initial_capital: float,
) -> List[Dict[str, Any]]:
    """Returns ``[{"date": "YYYY-MM-DD", "equity": float}, ...]`` -- the same
    shape strategy_testing/report.py's build_report expects.

    Returns an empty curve (rather than raising) when no daily bars are
    available for the requested window -- callers must treat that as "this
    symbol/window can't be backtested," not a hard error."""
    symbols = [s.upper() for s in symbols]
    daily_closes: Dict[str, Dict[str, float]] = {}
    for symbol in symbols:
        try:
            bars = get_futures_daily_bars(symbol, start.isoformat(), end.isoformat())
        except MarketDataUnavailableError:
            continue
        daily_closes[symbol] = {bar["t"]: bar["c"] for bar in bars}

    trading_days = sorted({d for closes in daily_closes.values() for d in closes})
    if not trading_days:
        return []

    cash = float(initial_capital)
    open_positions: List[_OpenPosition] = []
    curve: List[Dict[str, Any]] = []
    last_known_prices: Dict[str, float] = {}
    close_history: Dict[str, list] = {}

    for day in trading_days:
        prices_today = {s: closes[day] for s, closes in daily_closes.items() if day in closes}
        # prev_close must be read from last_known_prices BEFORE it's updated
        # with today's prices below -- a strategy comparing price vs
        # prev_close needs yesterday's close, not today's. None on day 1 for
        # every symbol (no prior close exists yet), same as a fresh live
        # engine tick before any quote history has accumulated.
        # quotes[sym]["closes"] (added 2026-08-29): trailing daily closes,
        # oldest -> newest, ending at today's price -- carried inside the
        # quote dict so the sandbox contract and existing strategies stay
        # untouched. Accumulates from the window's first day, so a strategy
        # needing N closes starts trading N days in.
        quotes = {}
        for s, p in prices_today.items():
            hist = close_history.setdefault(s, [])
            hist.append(p)
            if len(hist) > CLOSES_LOOKBACK:
                del hist[: len(hist) - CLOSES_LOOKBACK]
            quotes[s] = {"price": p, "prev_close": last_known_prices.get(s), "closes": list(hist)}
        last_known_prices.update(prices_today)

        equity_before = cash + _mark_to_market(open_positions, prices_today)
        intents = decide(day, _positions_payload(open_positions), quotes, {"cash": cash, "equity": equity_before})

        for intent in intents:
            symbol = intent["symbol"]
            if symbol not in prices_today:
                continue  # only trade what's actually priceable today
            price = prices_today[symbol]
            qty = intent["qty"]

            if intent["action"] == "open":
                cost_or_credit = qty * price
                # A real futures contract's notional value (price * qty) is
                # usually many times the $1,000 simulated wallet this
                # dashboard uses everywhere -- without this check, "buy 1
                # ES=F" against $1,000 cash silently traded on unlimited
                # implicit leverage, and a live-verification run against real
                # data produced a 529% annual return that turned out to be
                # exactly that bug, not a good strategy. Applied to both buy
                # and sell (a short's notional risk is the same magnitude as
                # a long's), and checked against the CURRENT cash balance so
                # it's aware of capital already committed to earlier opens
                # this same day or on prior days.
                if cost_or_credit > cash:
                    continue
                cash += -cost_or_credit if intent["side"] == "buy" else cost_or_credit
                open_positions.append(_OpenPosition(symbol=symbol, side=intent["side"], qty=qty, entry_price=price))
            else:  # close
                matching = next((p for p in open_positions if p.symbol == symbol), None)
                if matching is None:
                    continue
                proceeds = matching.qty * price
                cash += proceeds if matching.side == "buy" else -proceeds
                open_positions.remove(matching)

        equity_today = cash + _mark_to_market(open_positions, last_known_prices)
        curve.append({"date": day, "equity": equity_today})

    return curve


def run_backtest(
    code: str,
    symbols: List[str],
    start: date_cls,
    end: date_cls,
    initial_capital: float,
    *,
    timeout_per_day: int = 10,
) -> List[Dict[str, Any]]:
    def decide(as_of, positions, quotes, account):
        return run_decide_futures(
            code, as_of=as_of, positions=positions, quotes=quotes, account=account, timeout=timeout_per_day,
        )

    return _run_daybyday(decide, symbols, start, end, initial_capital)


def _llm_decide(client, model: Optional[str], prompt: str, as_of, positions, quotes, account) -> List[Dict[str, Any]]:
    from dashboard.backend.infrastructure.llm.backtest_harness import extract_response_text

    try:
        response = client.messages.create(
            model=model or "claude-haiku-4-5-20251001",
            max_tokens=800,
            system=_LLM_AGENT_SYSTEM_PROMPT.format(prompt=prompt),
            messages=[{
                "role": "user",
                "content": json.dumps({"as_of": as_of, "positions": positions, "quotes": quotes, "account": account}),
            }],
        )
        text = extract_response_text(response).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        raw = json.loads(text.strip())
    except Exception as exc:  # noqa: BLE001 -- a bad/unparsable LLM turn must not crash the run
        print(f"futures llm-agent backtest: decision failed on {as_of}: {exc}")
        return []
    return _clean_intents(raw)


def run_llm_agent_backtest(
    prompt: str,
    model: Optional[str],
    symbols: List[str],
    start: date_cls,
    end: date_cls,
    initial_capital: float,
    *,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Same day-by-day loop as ``run_backtest``, driven by an LLM reading the
    agent's own pipeline instruction instead of sandboxed code. Returns an
    empty curve when no LLM client is available (no key configured) rather
    than raising, matching every other decision-source fallback in this repo."""
    from dashboard.backend.infrastructure.llm.providers import make_llm_client

    client = make_llm_client(user_id=user_id)
    if client is None:
        return []

    def decide(as_of, positions, quotes, account):
        return _llm_decide(client, model, prompt, as_of, positions, quotes, account)

    return _run_daybyday(decide, symbols, start, end, initial_capital)
