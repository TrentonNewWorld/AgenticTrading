"""The Prediction dashboard's core mechanic: a strategy is never backtested
against history. It is paper-traded FORWARD, one real calendar day at a
time, against live Kalshi/Polymarket market data, for exactly
domain.prediction.repository.WAITING_DAYS_REQUIRED (5) real days, with fees
applied on every fill -- see domain/prediction/fees.py. Only once that
window completes does a result exist to show.

This is deliberately different from every other dashboard in this repo,
which all backtest a strategy instantly over the most recently completed
year of historical data. Prediction-market prices move on news and resolve
discretely (a market that looked like a coin flip yesterday can gap to
near-zero or near-one on a single headline), so a historical replay would
overstate what a user should actually expect going in -- the delay and the
up-front warning shown at submission time are both intentional risk
controls, not a rate limit to route around. See CLAUDE.md's Prediction
section for the full rationale, and domain/prediction/repository.py's
module docstring for why all three creation paths (Manual, My Agents,
Testing/Upload) converge on one table and one tick mechanic instead of the
three-separate-flows pattern every other asset class uses.

``tick_all`` is the scheduler's entry point (domain/prediction/scheduler.py),
called at most once per real day. It is idempotent per day: a strategy
already ticked for ``as_of`` is not in ``repository.list_due_for_tick``'s
result and is skipped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dashboard.backend.domain.prediction import fees, repository as repo
from dashboard.backend.domain.prediction.sandbox import _clean_intents, run_decide_prediction

_LLM_AGENT_SYSTEM_PROMPT = """You are trading prediction markets in a real forward paper-test for an \
agent with a fixed strategy instruction. Today is one of 5 real trading days this test runs for. You \
receive today's date, currently-held positions, today's active markets across Kalshi and Polymarket \
(each with its outcomes and current prices, 0-1), and account cash/equity. Follow the strategy \
instruction below exactly. Respond with ONLY a JSON array of order intents -- no prose, no markdown \
fences: [{{"action": "open"|"close", "platform": "kalshi"|"polymarket", "market_id": "...", \
"outcome": "...", "side": "buy"|"sell", "qty": number}}, ...]. "market_id" and "outcome" must be taken \
exactly from the markets you were shown. qty is a positive number of contracts/shares. Return [] to do \
nothing today.

Strategy instruction:
{prompt}"""


def fetch_active_markets(*, limit: int = 20) -> List[Dict[str, Any]]:
    """Today's tradeable universe, normalized across both platforms into one
    shape: {platform, market_id, title, outcomes: [{name, price}], close_time}.
    A platform whose API is unreachable today contributes nothing rather
    than failing the whole tick -- see the try/except below."""
    markets: List[Dict[str, Any]] = []

    try:
        from dashboard.backend.infrastructure.market_data.kalshi_markets import (
            MarketDataUnavailableError as KalshiUnavailable,
            list_active_markets as kalshi_list,
        )
        for m in kalshi_list(limit=limit):
            yes_price = m.get("last_price_dollars")
            try:
                yes_price = float(yes_price) if yes_price is not None else None
            except (TypeError, ValueError):
                yes_price = None
            if yes_price is None:
                # No trade yet today -- fall back to the ask (a fair current
                # price to transact at) rather than skipping the market.
                try:
                    yes_price = float(m.get("yes_ask_dollars") or 0)
                except (TypeError, ValueError):
                    yes_price = 0.0
            markets.append({
                "platform": "kalshi",
                "market_id": m.get("ticker"),
                "title": m.get("event_title") or m.get("ticker"),
                "outcomes": [
                    {"name": "yes", "price": round(yes_price, 4)},
                    {"name": "no", "price": round(1 - yes_price, 4)},
                ],
                "close_time": m.get("close_time"),
            })
    except KalshiUnavailable as exc:
        print(f"prediction engine: Kalshi market data unavailable this tick: {exc}")
    except Exception as exc:  # noqa: BLE001 -- one platform's failure must not sink the tick
        print(f"prediction engine: Kalshi market fetch failed: {exc}")

    try:
        from dashboard.backend.infrastructure.market_data.polymarket_markets import (
            MarketDataUnavailableError as PolymarketUnavailable,
            list_active_markets as polymarket_list,
        )
        for m in polymarket_list(limit=limit):
            outcome_prices = m.get("outcome_prices") or {}
            if not outcome_prices:
                continue
            markets.append({
                "platform": "polymarket",
                "market_id": m.get("conditionId"),
                "title": m.get("question"),
                "outcomes": [{"name": name, "price": round(price, 4)} for name, price in outcome_prices.items()],
                "close_time": m.get("endDate"),
            })
    except PolymarketUnavailable as exc:
        print(f"prediction engine: Polymarket market data unavailable this tick: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"prediction engine: Polymarket market fetch failed: {exc}")

    return markets


def _index_markets(markets: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    return {(m["platform"], m["market_id"]): m for m in markets if m.get("market_id")}


def _price_for(market: Dict[str, Any], outcome: str) -> Optional[float]:
    for o in market.get("outcomes", []):
        if o.get("name") == outcome:
            return o.get("price")
    return None


def _positions_payload(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"platform": p["platform"], "market_id": p["market_id"], "outcome": p["outcome"],
         "side": p["side"], "qty": p["qty"]}
        for p in positions
    ]


def _mark_to_market(positions: List[Dict[str, Any]], markets_by_id: Dict[Tuple[str, str], Dict[str, Any]]) -> float:
    total = 0.0
    for pos in positions:
        market = markets_by_id.get((pos["platform"], pos["market_id"]))
        price = _price_for(market, pos["outcome"]) if market else None
        if price is None:
            price = pos.get("entry_price", 0.5)
        value = pos["qty"] * price
        total += value if pos["side"] == "buy" else -value
    return total


def _kalshi_credentials_for(user_id: Optional[int]) -> Optional[Dict[str, str]]:
    """A signed-in user's connected Kalshi credentials, if any. Mirrors
    infrastructure/brokers/credentials.py's resolve_alpaca_credentials:
    user_id=None (the unattended scheduler tick has no signed-in caller to
    resolve against for a manual/upload strategy) always returns None, and
    the caller falls back to local simulation -- never an error."""
    if user_id is None:
        return None
    from dashboard.backend.domain.connections.repository import connection_store

    return connection_store.get_credentials(int(user_id), "kalshi")


def _place_real_kalshi_order(intent: Dict[str, Any], credentials: Dict[str, str]) -> bool:
    """Best-effort real order against Kalshi's free demo exchange (never
    production -- see infrastructure/brokers/kalshi_paper.py's module
    docstring for why the demo/production split exists and why this repo
    only ever reaches for demo). Returns whether it actually placed;
    swallows every failure (network, rejected order, bad credentials) since
    the LOCAL simulated fill below is the one this dashboard's own equity
    curve and 5-day mechanic depend on -- a real-order failure must not
    corrupt that bookkeeping, only mean this fill stayed simulated."""
    from dashboard.backend.infrastructure.brokers.kalshi_paper import (
        KalshiClient, KalshiConfigError, KalshiCredentials, KalshiOrderError,
    )

    try:
        client = KalshiClient(
            credentials=KalshiCredentials(
                api_key_id=credentials["api_key"], private_key_pem=credentials["secret_key"],
            ),
            environment="demo",
        )
        client.place_order(
            ticker=intent["market_id"], side=intent["outcome"], action=intent["side"], count=int(intent["qty"]),
        )
        return True
    except (KalshiConfigError, KalshiOrderError, KeyError, ValueError, TypeError) as exc:
        print(f"prediction engine: real Kalshi order failed (falling back to simulated fill only): {exc}")
        return False


def _polymarket_credentials_for(user_id: Optional[int]) -> Optional[Dict[str, str]]:
    """A signed-in user's connected Polymarket wallet key, if any -- mirrors
    ``_kalshi_credentials_for``. Checking this is deliberately cheap and
    side-effect-free; the operator-level ``POLYMARKET_EXECUTE`` half of the
    double-gate is checked separately in ``_place_real_polymarket_order``,
    not here, so a caller can tell "no key connected" apart from "connected
    but the operator hasn't armed real execution" if it ever needs to."""
    if user_id is None:
        return None
    from dashboard.backend.domain.connections.repository import connection_store

    return connection_store.get_credentials(int(user_id), "polymarket")


def _place_real_polymarket_order(intent: Dict[str, Any], price: float, credentials: Dict[str, str]) -> bool:
    """Best-effort real order against Polymarket -- real money, no demo
    fallback (see infrastructure/brokers/polymarket_paper.py's module
    docstring). Gated on BOTH the caller having connected a wallet key
    (checked by the caller via ``_polymarket_credentials_for``) AND the
    operator having armed ``POLYMARKET_EXECUTE`` -- unlike Kalshi's demo
    path, there is no fake-money floor under a mistake here, so this is the
    one real-order path in this engine with a second, operator-level gate.
    Swallows every failure for the same reason ``_place_real_kalshi_order``
    does: the local simulated fill is what this dashboard's equity curve and
    5-day mechanic depend on and must never be corrupted by a real-order
    failure."""
    from dashboard.backend.infrastructure.brokers.polymarket_paper import (
        PolymarketClient, PolymarketConfigError, PolymarketCredentials, PolymarketOrderError, execute_enabled,
    )

    if not execute_enabled():
        return False
    try:
        client = PolymarketClient(
            credentials=PolymarketCredentials(wallet_private_key=credentials["api_key"]),
        )
        client.place_order(token_id=intent["market_id"], side=intent["side"], size=intent["qty"], price=price)
        return True
    except (PolymarketConfigError, PolymarketOrderError, KeyError, ValueError, TypeError) as exc:
        print(f"prediction engine: real Polymarket order failed (falling back to simulated fill only): {exc}")
        return False


def _apply_intents(
    intents: List[Dict[str, Any]],
    markets_by_id: Dict[Tuple[str, str], Dict[str, Any]],
    cash: float,
    positions: List[Dict[str, Any]],
    *,
    user_id: Optional[int] = None,
) -> Tuple[float, List[Dict[str, Any]], float]:
    """Same cash-sufficiency-cap pattern as every other asset class's engine
    (futures/forex/crypto) -- an open that would exceed available cash is
    refused, not silently traded on implicit leverage. Every fill also pays
    a real per-platform fee (domain/prediction/fees.py), debited from cash
    regardless of which side of the trade it is.

    The local simulated fill below always happens and is always what this
    strategy's equity curve tracks -- a real order is placed *alongside* it,
    best-effort, never instead of it, whenever the strategy's owner has
    connected that platform's credentials: Kalshi against the free demo
    exchange (see ``_kalshi_credentials_for``); Polymarket only when the
    operator has ALSO armed ``POLYMARKET_EXECUTE`` -- real money, no demo
    floor, see ``_place_real_polymarket_order``'s docstring for the
    double-gate reasoning."""
    kalshi_credentials = _kalshi_credentials_for(user_id)
    polymarket_credentials = _polymarket_credentials_for(user_id)
    fees_paid = 0.0
    for intent in intents:
        key = (intent["platform"], intent["market_id"])
        market = markets_by_id.get(key)
        if market is None:
            continue
        price = _price_for(market, intent["outcome"])
        if price is None:
            continue
        qty = intent["qty"]
        fee = fees.fee_for(intent["platform"], qty, price)

        if intent["action"] == "open":
            cost_or_credit = qty * price
            if cost_or_credit + fee > cash:
                continue  # would exceed the wallet
            cash += -cost_or_credit if intent["side"] == "buy" else cost_or_credit
            cash -= fee
            fees_paid += fee
            if intent["platform"] == "kalshi" and kalshi_credentials:
                _place_real_kalshi_order(intent, kalshi_credentials)
            elif intent["platform"] == "polymarket" and polymarket_credentials:
                _place_real_polymarket_order(intent, price, polymarket_credentials)
            positions.append({
                "platform": intent["platform"], "market_id": intent["market_id"], "outcome": intent["outcome"],
                "side": intent["side"], "qty": qty, "entry_price": price,
            })
        else:  # close
            matching = next(
                (p for p in positions if p["platform"] == intent["platform"] and p["market_id"] == intent["market_id"]
                 and p["outcome"] == intent["outcome"]),
                None,
            )
            if matching is None:
                continue
            proceeds = matching["qty"] * price
            if fee > cash + (proceeds if matching["side"] == "buy" else -proceeds):
                continue  # closing must not itself drive cash irrecoverably negative on the fee alone
            cash += proceeds if matching["side"] == "buy" else -proceeds
            cash -= fee
            fees_paid += fee
            if intent["platform"] == "kalshi" and kalshi_credentials:
                # Closing means trading the opposite side of the held
                # position, same convention as opening it.
                close_intent = dict(intent, side="sell" if matching["side"] == "buy" else "buy")
                _place_real_kalshi_order(close_intent, kalshi_credentials)
            elif intent["platform"] == "polymarket" and polymarket_credentials:
                close_intent = dict(intent, side="sell" if matching["side"] == "buy" else "buy")
                _place_real_polymarket_order(close_intent, price, polymarket_credentials)
            positions.remove(matching)

    return cash, positions, fees_paid


def _decide_for(strategy: Dict[str, Any], as_of: str, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    account = {"cash": strategy["cash"], "equity": strategy["cash"] + _mark_to_market(strategy["positions"], _index_markets(markets))}
    positions_payload = _positions_payload(strategy["positions"])

    if strategy["source_type"] in ("manual", "upload"):
        return run_decide_prediction(
            strategy["code"], as_of=as_of, positions=positions_payload, markets=markets, account=account,
        )

    if strategy["source_type"] == "agent":
        from dashboard.backend.infrastructure.llm.providers import make_llm_client
        from dashboard.backend.infrastructure.llm.backtest_harness import extract_response_text
        import json as _json

        client = make_llm_client(user_id=strategy.get("user_id"))
        if client is None:
            return []
        try:
            response = client.messages.create(
                model=strategy.get("model") or "claude-haiku-4-5-20251001",
                max_tokens=800,
                system=_LLM_AGENT_SYSTEM_PROMPT.format(prompt=strategy["strategy_prompt"]),
                messages=[{
                    "role": "user",
                    "content": _json.dumps({"as_of": as_of, "positions": positions_payload, "markets": markets, "account": account}),
                }],
            )
            text = extract_response_text(response).strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:]
            raw = _json.loads(text.strip())
        except Exception as exc:  # noqa: BLE001 -- a bad LLM turn must not crash the tick
            print(f"prediction engine: agent decision failed for {strategy['id']} on {as_of}: {exc}")
            return []
        return _clean_intents(raw)

    return []


def tick_all(as_of: Optional[str] = None) -> List[Dict[str, Any]]:
    """Advance every strategy that hasn't been ticked yet today by exactly
    one day. Returns the list of updated strategy dicts. Safe to call more
    than once on the same day (idempotent -- see
    repository.list_due_for_tick's docstring); the scheduler still only
    calls it once, this is a defensive property, not a relied-on retry path.
    """
    as_of = as_of or datetime.now(timezone.utc).date().isoformat()
    due = repo.list_due_for_tick(as_of)
    if not due:
        return []

    markets = fetch_active_markets(limit=40)
    markets_by_id = _index_markets(markets)

    updated = []
    for strategy in due:
        try:
            intents = _decide_for(strategy, as_of, markets)
            new_cash, new_positions, fees_paid = _apply_intents(
                intents, markets_by_id, strategy["cash"], list(strategy["positions"]),
                user_id=strategy.get("user_id"),
            )
            equity_today = new_cash + _mark_to_market(new_positions, markets_by_id)
            result = repo.record_tick(
                strategy["id"], as_of=as_of, cash=new_cash, positions=new_positions,
                equity_point={"date": as_of, "equity": equity_today}, fees_paid_today=fees_paid,
            )
            updated.append(result)
        except Exception as exc:  # noqa: BLE001 -- one strategy's failure must not sink the whole tick cycle
            print(f"prediction engine: tick failed for {strategy['id']}: {exc}")
            repo.mark_error(strategy["id"], error=str(exc))

    return updated
