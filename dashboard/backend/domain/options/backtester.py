"""Day-by-day event-driven backtest for full-contract-level options
strategies, and for My Agents' LLM-driven options agents.

Sub-phase 6 of the Options-dashboard plan -- the highest-risk piece, per the
plan, because it has to reconstruct history Alpaca cannot query directly
(see ``domain/options/contracts.py``'s module docstring: no "what was listed
on this past date" API, so candidate contracts are synthesized and probed
for real bars). Structurally similar to ``strategy_testing/simulator.py``'s
day loop, but tracks contract legs (strike/expiration/right/side) instead of
share weights, and has to handle **expiration** -- something no equity
strategy backtest deals with at all: a held contract that reaches its
expiration date is settled that day, not carried forward.

Also handles **stock legs** (``leg_role="stock"``, an ordinary equity
position at the standard 1-share multiplier) alongside option legs, since a
covered call (Sub-phase 8's starter roster) is long the underlying + short a
call on it -- both leg types share one cash/position ledger below, and only
option legs ever go through expiration settlement (a stock leg has no
expiration and is simply marked to market every day until the strategy
closes it).

Settlement model (cash-settled at expiration, the standard simplification
for a backtest -- no early-assignment/physical-delivery modeling):

* A **long** option leg pays nothing to open beyond the premium; at
  expiration it receives intrinsic value if in-the-money, nothing if
  out-of-the-money ("expires worthless").
* A **short** option leg receives the premium up front; at expiration it
  *pays* intrinsic value if in-the-money (the credit already received is its
  only profit if OTM).

Intrinsic value: ``max(0, underlying_close - strike)`` for a call,
``max(0, strike - underlying_close)`` for a put, times 100 (standard
contract multiplier) times quantity.

Confirmed feasible against real data in the Sub-phase 1 spike
(dashboard/backend/scripts/spike_options_data_findings.md): synthesized
candidates found real historical bars 6/12/18 months back on the free feed.

``run_backtest`` (sandboxed-code Manual/Testing strategies) and
``run_llm_agent_backtest`` (My Agents pipelines) share the same
``_run_daybyday`` loop -- expiration settlement, chain building, fills --
and differ only in how a day's order intents are decided: a code sandbox
call vs an LLM call. Both converge on ``_clean_intents``
(domain/options/sandbox.py) for the same reason every other asset class's
pair does: an LLM's JSON response needs exactly the same untrusted-input
validation a sandboxed script's stdout does. The LLM is handed the OCC
symbol for each candidate contract rather than a separate strike/expiration/
right triple -- ``domain/options/contracts.py::parse_occ_symbol`` already
decodes those from it, so the model just echoes back the symbol it picked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Any, Callable, Dict, List, Optional

from dashboard.backend.domain.options.contracts import (
    _fetch_underlying_daily_closes,
    find_candidate_contracts,
)
from dashboard.backend.domain.options.sandbox import _clean_intents, run_decide_options
from dashboard.backend.infrastructure.market_data.alpaca_options import get_option_bars

#: Standard options contract multiplier (1 contract = 100 shares of intrinsic
#: exposure). Not configurable -- every US-listed equity/index option this
#: backtester deals with uses it. A stock leg uses a multiplier of 1 (an
#: ordinary share).
OPTION_CONTRACT_MULTIPLIER = 100
STOCK_MULTIPLIER = 1

DecideFn = Callable[[str, List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]], Dict[str, Any]], List[Dict[str, Any]]]

_LLM_AGENT_SYSTEM_PROMPT = """You are trading options in a paper backtest for an agent with a fixed \
strategy instruction. At each step you receive today's date, currently-held legs (option or stock), \
today's option chain per underlying (each contract's OCC symbol, strike, expiration, right, and \
price), and account cash/equity. Follow the strategy instruction below exactly. Respond with ONLY a \
JSON array of order intents -- no prose, no markdown fences: [{{"action": "open"|"close", "symbol": \
"...", "side": "buy"|"sell", "qty": integer, "leg_role": "option"|"stock"|"single"}}, ...]. "symbol" \
is either an OCC option symbol taken exactly from the chain, or a bare underlying ticker for a stock \
leg. qty is a positive whole number of contracts (or shares for a stock leg). Return [] to do nothing \
this step.

Strategy instruction:
{prompt}"""


@dataclass
class _OpenLeg:
    symbol: str
    underlying: str
    side: str  # "buy" (long) | "sell" (short)
    qty: int
    entry_price: float  # premium per share (options) or price per share (stock)
    leg_role: str = "single"
    # Option-only fields; None for a stock leg (which never auto-settles).
    strike: Optional[float] = None
    expiration: Optional[date_cls] = None
    right: Optional[str] = None

    @property
    def multiplier(self) -> int:
        return STOCK_MULTIPLIER if self.leg_role == "stock" else OPTION_CONTRACT_MULTIPLIER

    @property
    def is_option(self) -> bool:
        return self.expiration is not None


def _intrinsic_value(right: str, strike: float, underlying_close: float) -> float:
    if right == "C":
        return max(0.0, underlying_close - strike)
    return max(0.0, strike - underlying_close)


def _mark_to_market(legs: List[_OpenLeg], prices_today: Dict[str, float]) -> float:
    """Signed exposure value of every open leg at today's prices (or its own
    last-known price if untraded/unpriced today) -- long legs are an asset,
    short legs are a liability."""
    total = 0.0
    for leg in legs:
        price = prices_today.get(leg.symbol, leg.entry_price)
        value = leg.qty * price * leg.multiplier
        total += value if leg.side == "buy" else -value
    return total


def _positions_payload(legs: List[_OpenLeg]) -> List[Dict[str, Any]]:
    return [
        {
            "symbol": leg.symbol, "underlying": leg.underlying,
            "strike": leg.strike, "expiration": leg.expiration.isoformat() if leg.expiration else None,
            "right": leg.right, "qty": leg.qty, "leg_role": leg.leg_role,
        }
        for leg in legs
    ]


def _run_daybyday(
    decide: DecideFn,
    underlyings: List[str],
    start: date_cls,
    end: date_cls,
    initial_capital: float,
) -> List[Dict[str, Any]]:
    """Returns ``[{"date": "YYYY-MM-DD", "equity": float}, ...]`` -- the same
    shape ``strategy_testing/report.py``'s ``build_report`` expects (reused
    unchanged via ``domain/options/report.py``).

    Returns an empty curve (rather than raising) when no candidate contracts
    or underlying price history are available for the requested window --
    callers must treat that as "this underlying/window can't be backtested,"
    not a hard error, matching ``find_candidate_contracts``'s own posture."""
    underlyings = [u.upper() for u in underlyings]
    underlying_set = set(underlyings)

    all_candidates = []
    for underlying in underlyings:
        all_candidates.extend(find_candidate_contracts(underlying, start, end))
    if not all_candidates:
        return []

    candidate_symbols = [c.symbol for c in all_candidates]
    option_bars_by_symbol = get_option_bars(candidate_symbols, start, end)
    if not option_bars_by_symbol:
        return []

    contract_meta = {c.symbol: c for c in all_candidates}

    # {day: {option_symbol: close_price}} -- built once, not re-fetched per day.
    option_daily_prices: Dict[date_cls, Dict[str, float]] = {}
    for symbol, frame in option_bars_by_symbol.items():
        for ts, row in frame.iterrows():
            day = ts.date() if hasattr(ts, "date") else ts
            option_daily_prices.setdefault(day, {})[symbol] = float(row["close"])

    underlying_closes: Dict[str, Dict[date_cls, float]] = {
        u: _fetch_underlying_daily_closes(u, start, end) for u in underlyings
    }

    # Trading days come from the UNDERLYING's own daily closes, not from
    # option-bar presence: an option routinely stops trading (zero volume,
    # no bar) a day or more before its own expiration, and expiration must
    # still be visited and settled on its actual date regardless. Equities
    # trade essentially every session, so their closes are the reliable
    # calendar; option-bar-only days are folded in too, in case a contract
    # traded on a day this repo's equity fetch didn't cover.
    trading_days = sorted(
        {d for closes in underlying_closes.values() for d in closes} | set(option_daily_prices.keys())
    )
    if not trading_days:
        return []

    cash = float(initial_capital)
    open_legs: List[_OpenLeg] = []
    curve: List[Dict[str, Any]] = []
    last_known_prices: Dict[str, float] = {}

    for day in trading_days:
        # One combined price map for the day: option closes plus each
        # underlying's own close (so a stock leg can be priced/traded too).
        prices_today: Dict[str, float] = dict(option_daily_prices.get(day, {}))
        for underlying, closes in underlying_closes.items():
            if day in closes:
                prices_today[underlying] = closes[day]
        last_known_prices.update(prices_today)

        # -- Expiration settlement: anything expiring today is closed out
        # BEFORE the day's decision call, so a strategy never sees an
        # already-expired leg in its own positions list. Stock legs
        # (expiration=None) are never touched here.
        still_open: List[_OpenLeg] = []
        for leg in open_legs:
            if not leg.is_option or leg.expiration > day:
                still_open.append(leg)
                continue
            underlying_close = underlying_closes.get(leg.underlying, {}).get(day)
            if underlying_close is None:
                # No underlying price on the exact expiration day (a market
                # holiday landed on it, or missing data) -- carry one more
                # day rather than settle blind; this is rare in practice
                # since equity options always expire on a trading day.
                still_open.append(leg)
                continue
            intrinsic = _intrinsic_value(leg.right, leg.strike, underlying_close)
            settlement = leg.qty * intrinsic * leg.multiplier
            cash += settlement if leg.side == "buy" else -settlement
        open_legs = still_open

        # -- Build today's chain: only contracts with a real bar today, so
        # the decision step never sees a contract that wasn't actually
        # priceable (no look-ahead, no phantom liquidity).
        chain: Dict[str, List[Dict[str, Any]]] = {}
        for symbol, price in option_daily_prices.get(day, {}).items():
            meta = contract_meta.get(symbol)
            if meta is None:
                continue
            chain.setdefault(meta.underlying, []).append({
                "symbol": symbol, "strike": meta.strike,
                "expiration": meta.expiration.isoformat(), "right": meta.right,
                "bid": price, "ask": price, "last": price, "open_interest": None,
            })

        if chain:
            equity_before = cash + _mark_to_market(open_legs, prices_today)
            intents = decide(day.isoformat(), _positions_payload(open_legs), chain, {"cash": cash, "equity": equity_before})

            for intent in intents:
                symbol = intent["symbol"]
                meta = contract_meta.get(symbol)
                is_stock_leg = meta is None and symbol in underlying_set
                if meta is None and not is_stock_leg:
                    continue  # neither a priceable option nor a known underlying
                if symbol not in prices_today:
                    continue  # only trade what's actually priceable today
                price = prices_today[symbol]
                qty = intent["qty"]
                multiplier = STOCK_MULTIPLIER if is_stock_leg else OPTION_CONTRACT_MULTIPLIER

                if intent["action"] == "open":
                    cost_or_credit = qty * price * multiplier
                    # Same cash-sufficiency cap Futures/Forex/Crypto all have
                    # (see their backtester.py docstrings for the live-
                    # verification incident that found this missing there
                    # first) -- but unlike those single-instrument engines,
                    # applied to "buy" (debit) opens ONLY, not "sell". A short
                    # option/stock leg here can legitimately be COVERED by
                    # another leg in the same multi-leg strategy (a covered
                    # call's short call is covered by its long stock, not by
                    # spare cash) -- capping it against raw cash the same way
                    # a long open is capped would refuse this dashboard's own
                    # opt_covered_call_starter strategy outright. A short
                    # open's real risk shows up honestly at settlement
                    # instead (the code above that pays out intrinsic value),
                    # which is the correct place for it to draw down cash,
                    # not at entry, where selling is a credit, not a debit.
                    if intent["side"] == "buy" and cost_or_credit > cash:
                        continue
                    cash += -cost_or_credit if intent["side"] == "buy" else cost_or_credit
                    if is_stock_leg:
                        open_legs.append(_OpenLeg(
                            symbol=symbol, underlying=symbol, side=intent["side"], qty=qty,
                            entry_price=price, leg_role="stock",
                        ))
                    else:
                        open_legs.append(_OpenLeg(
                            symbol=symbol, underlying=meta.underlying, side=intent["side"], qty=qty,
                            entry_price=price, leg_role=intent.get("leg_role", "single"),
                            strike=meta.strike, expiration=meta.expiration, right=meta.right,
                        ))
                else:  # close
                    matching = next((leg for leg in open_legs if leg.symbol == symbol), None)
                    if matching is None:
                        continue
                    proceeds = matching.qty * price * matching.multiplier
                    # Closing a long leg sells it (credit); closing a short
                    # leg buys it back (debit) -- opposite of the entry cash flow.
                    cash += proceeds if matching.side == "buy" else -proceeds
                    open_legs.remove(matching)

        equity_today = cash + _mark_to_market(open_legs, last_known_prices)
        curve.append({"date": day.isoformat(), "equity": equity_today})

    return curve


def run_backtest(
    code: str,
    underlyings: List[str],
    start: date_cls,
    end: date_cls,
    initial_capital: float,
    *,
    timeout_per_day: int = 10,
) -> List[Dict[str, Any]]:
    def decide(as_of, positions, chain, account):
        return run_decide_options(
            code, as_of=as_of, positions=positions, chain=chain, account=account, timeout=timeout_per_day,
        )

    return _run_daybyday(decide, underlyings, start, end, initial_capital)


def _llm_decide(client, model: Optional[str], prompt: str, as_of, positions, chain, account) -> List[Dict[str, Any]]:
    from dashboard.backend.infrastructure.llm.backtest_harness import extract_response_text

    try:
        response = client.messages.create(
            model=model or "claude-haiku-4-5-20251001",
            max_tokens=800,
            system=_LLM_AGENT_SYSTEM_PROMPT.format(prompt=prompt),
            messages=[{
                "role": "user",
                "content": json.dumps({"as_of": as_of, "positions": positions, "chain": chain, "account": account}),
            }],
        )
        text = extract_response_text(response).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        raw = json.loads(text.strip())
    except Exception as exc:  # noqa: BLE001 -- a bad/unparsable LLM turn must not crash the run
        print(f"options llm-agent backtest: decision failed on {as_of}: {exc}")
        return []
    return _clean_intents(raw)


def run_llm_agent_backtest(
    prompt: str,
    model: Optional[str],
    underlyings: List[str],
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

    def decide(as_of, positions, chain, account):
        return _llm_decide(client, model, prompt, as_of, positions, chain, account)

    return _run_daybyday(decide, underlyings, start, end, initial_capital)
