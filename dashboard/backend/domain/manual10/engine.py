"""Manual: one `tick()` call per scheduler wakeup, dispatching across every
strategy the user has *activated* for today (see repository.py's
activations table -- selecting a strategy just opens its panel; activating
is the separate, explicit action that lets it actually trade).

Built-in "Top 10" state machine, per activated (trading_date, strategy_key):
  pending  -- before the screener window opens; the wallet snapshot is taken
              here, once, right before the market opens.
  screening -- the first `screener_window_minutes` of the session; candidates
              are (re)computed every tick so the UI's loading bar has
              something live to show, but the pick is only finalized once the
              window ends.
  holding  -- the picks are bought in paper, then watched: each position gets
              exactly one promotion check at
              entry_time + promotion_window_minutes (not continuous
              monitoring -- "did it grow in the 6 minutes after we bought it"
              is a single checkpoint, per the feature's own spec).
  closing  -- from `close_out_minutes_before_close` before the close, sell
              everything; `straggler_check_minutes_before_close` is a second
              sweep for anything the first pass missed.
  closed   -- the trading day is settled: every position has an exit price,
              and today's combined (paper + real) P&L is recorded for the
              wallet-balance calendar.

Uploaded strategies tick on their own fixed interval instead (see
``uploads.py``'s ``tick_uploaded_strategy``) -- they have no opening-range
screener/promotion choreography of their own, just a sandboxed decide() call
rebalancing paper positions.

Real-money safety: buying/selling in the *paper* bucket goes through
``AlpacaPaperTradingClient`` gated by the same ``ALPACA_PAPER_EXECUTE`` switch
every other paper-trading path in this app already uses. Promoting a position
to the *real* bucket -- and closing a real position -- additionally requires
``MANUAL10_ALLOW_REAL_MONEY=true`` on top of the existing
``ALPACA_LIVE_EXECUTE`` switch: unlike every other live-trading path in this
codebase (which places a real order only when a signed-in human clicks Run
with `dry_run=False`), this engine decides on its own, unattended, every
trading day -- so it gets its own explicit acknowledgment on top of the
account-wide live switch. With either switch off, a stock that would have
been promoted simply stays in paper with a `promotion_note` explaining why,
rather than fabricating a "real" position that was never actually bought
(see the "fail-closed is not fail-visible" rule in CLAUDE.md).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dashboard.backend.domain.manual10 import market_clock, repository as repo
from dashboard.backend.domain.manual10.repository import TOP_10_STRATEGY_KEY
from dashboard.backend.domain.manual10.screener import find_top_movers
from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient
from dashboard.backend.infrastructure.brokers.alpaca_live import AlpacaLiveTradingClient


def paper_execute_enabled() -> bool:
    return os.getenv("ALPACA_PAPER_EXECUTE", "false").strip().lower() in {"1", "true", "yes", "on"}


def live_execute_enabled() -> bool:
    return os.getenv("ALPACA_LIVE_EXECUTE", "false").strip().lower() in {"1", "true", "yes", "on"}


def real_money_promotion_allowed() -> bool:
    """The feature-specific acknowledgment on top of ALPACA_LIVE_EXECUTE --
    see the module docstring for why this engine needs a second switch that
    no other live-trading path in this codebase requires."""
    return os.getenv("MANUAL10_ALLOW_REAL_MONEY", "false").strip().lower() in {"1", "true", "yes", "on"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _paper_client() -> AlpacaPaperTradingClient:
    return AlpacaPaperTradingClient()


def _live_client() -> AlpacaLiveTradingClient:
    return AlpacaLiveTradingClient()


def _current_price(symbol: str, client) -> Optional[float]:
    quotes = client.get_quotes([symbol])
    return quotes.get(symbol.upper())


#: Manual's Top 10 tracks two independent position sets under one strategy
#: key (bucket="paper" vs bucket="real") -- but domain/leaderboard/
#: real_trading.py's ledger is keyed by strategy_key ALONE, one notional
#: sub-portfolio per key, so recording both buckets under "top_10" would
#: silently conflate two different position sets into one ledger. These are
#: the two distinct leaderboard-facing keys instead, each getting its own
#: independent notional sub-portfolio exactly like every other strategy on
#: that leaderboard.
TOP10_PAPER_LEADERBOARD_KEY = TOP_10_STRATEGY_KEY
TOP10_LIVE_LEADERBOARD_KEY = f"{TOP_10_STRATEGY_KEY}_live"


def _record_top10_leaderboard_snapshot(trading_date: str) -> None:
    """Feed Manual's Top 10 into the same Live Trading Leaderboard
    (domain/leaderboard/real_trading.py) the Strategy Catalog's Run in
    Paper/Live buttons already write to -- so a user can see what Manual is
    actually making, ranked alongside everything else there. Best-effort:
    a quote-fetch or bookkeeping failure here must never break the tick that
    actually manages Manual's real positions."""
    from dashboard.backend.domain.leaderboard.real_trading import record_real_trading_snapshot

    for bucket, leaderboard_key, mode, client_factory in (
        ("paper", TOP10_PAPER_LEADERBOARD_KEY, "paper", _paper_client),
        ("real", TOP10_LIVE_LEADERBOARD_KEY, "live", _live_client),
    ):
        try:
            open_positions = repo.list_positions(trading_date, TOP_10_STRATEGY_KEY, bucket=bucket, status="open")
            if not open_positions:
                continue  # no real trading activity yet -- leave no snapshot, matching this ledger's own "by design" rule
            client = client_factory()
            prices: Dict[str, float] = {}
            for pos in open_positions:
                prices[pos["symbol"]] = _current_price(pos["symbol"], client) or pos["entry_price"]
            total_value = sum(pos["shares"] * prices[pos["symbol"]] for pos in open_positions)
            if total_value <= 0:
                continue
            target_weights = {
                pos["symbol"]: (pos["shares"] * prices[pos["symbol"]]) / total_value for pos in open_positions
            }
            record_real_trading_snapshot(leaderboard_key, mode, target_weights, prices)
        except Exception as exc:
            print(f"manual10: leaderboard snapshot failed for {leaderboard_key}: {exc}")


#: Bounds one strategy's phase-transition cascade within a single tick() call
#: -- a scheduler that missed several transition boundaries (e.g. the process
#: was down) should catch up through every phase it's now already past,
#: rather than needing one call per phase. There are only 5 phases, so this
#: can never legitimately need more than 5 passes; it exists purely so a
#: future bug that flips phases in a cycle fails loudly instead of hanging.
_MAX_TRANSITIONS_PER_TICK = 5


def tick(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Advance every activated strategy's state by one step. Safe to call
    repeatedly and out of a fresh calendar day -- each call re-derives "today"
    from the live market calendar rather than trusting state left over from a
    previous run."""
    session = market_clock.get_today_session()
    if not session.has_session:
        return {"trading_date": str(session.trading_date), "phase": "no_session", "strategies": {}}

    trading_date = str(session.trading_date)
    results: Dict[str, Any] = {}
    for activation in repo.list_activations(trading_date):
        if not activation["activated"]:
            continue
        strategy_key = activation["strategy_key"]
        strategy_def = repo.get_strategy_def(strategy_key)
        if strategy_def is None:
            continue
        if strategy_def["kind"] == "builtin" and strategy_key == TOP_10_STRATEGY_KEY:
            results[strategy_key] = _tick_top10(trading_date, strategy_key, session)
            _record_top10_leaderboard_snapshot(trading_date)
        elif strategy_def["kind"] == "uploaded":
            from dashboard.backend.domain.manual10.uploads import tick_uploaded_strategy
            results[strategy_key] = tick_uploaded_strategy(trading_date, strategy_key, strategy_def, session)

    return {"trading_date": trading_date, "strategies": results}


def _tick_top10(trading_date: str, strategy_key: str, session) -> Dict[str, Any]:
    day = repo.ensure_day(trading_date, strategy_key)
    phase = day["phase"]

    if phase == "pending" and day.get("wallet_reset_amount") is None:
        _reset_wallet(trading_date, strategy_key)

    if session.now < session.open_at:
        return {"phase": phase}

    for _ in range(_MAX_TRANSITIONS_PER_TICK):
        settings = repo.get_settings()
        screener_window_end = session.open_at + timedelta(minutes=settings["screener_window_minutes"])
        close_out_start = session.close_at - timedelta(minutes=settings["close_out_minutes_before_close"])
        straggler_at = session.close_at - timedelta(minutes=settings["straggler_check_minutes_before_close"])

        if phase in ("pending", "screening") and session.now < screener_window_end:
            _run_screener_pass(trading_date, strategy_key, settings, final=False)
            if phase == "pending":
                repo.update_day(trading_date, strategy_key, phase="screening", screener_started_at=_iso(session.now))
            return {"phase": "screening"}

        if phase in ("pending", "screening") and session.now >= screener_window_end:
            _finalize_screener_and_buy(trading_date, strategy_key, settings)
            repo.update_day(trading_date, strategy_key, phase="holding", screener_completed_at=_iso(session.now))
            phase = "holding"
            continue

        if phase == "holding":
            _record_price_snapshots(trading_date, strategy_key)
            _check_promotions(trading_date, strategy_key, settings, session.now)
            if session.now >= close_out_start:
                repo.update_day(trading_date, strategy_key, phase="closing")
                phase = "closing"
                continue
            return {"phase": "holding"}

        if phase == "closing":
            _record_price_snapshots(trading_date, strategy_key)
            reason = "straggler" if session.now >= straggler_at else "close_out"
            _close_all_open_positions(trading_date, strategy_key, reason=reason)
            remaining = repo.list_positions(trading_date, strategy_key, status="open")
            if not remaining and session.now >= straggler_at:
                _finalize_day(trading_date, strategy_key, session.now)
                return {"phase": "closed"}
            return {"phase": "closing"}

        return {"phase": phase}

    return {"phase": phase}


def _reset_wallet(trading_date: str, strategy_key: str) -> None:
    """Snapshot the *real* (live) account's current equity as today's
    reference wallet amount -- "real money" per the feature's own spec, not
    the app's separate simulated $10k-per-user portfolio ledger
    (domain/portfolios/service.py, an unrelated concept -- see
    domain/manual10/__init__.py's cross-reference)."""
    try:
        account = _live_client().get_account()
    except Exception as exc:  # no live credentials configured, etc -- don't block the day on it
        print(f"manual10: could not read live account balance for wallet reset: {exc}")
        account = None
    amount = float(account["equity"]) if account else 0.0
    repo.update_day(trading_date, strategy_key, wallet_reset_amount=amount)


def _run_screener_pass(trading_date: str, strategy_key: str, settings: Dict[str, Any], *, final: bool) -> List[Dict[str, Any]]:
    try:
        picks = find_top_movers(
            top_n=settings["top_n"], price_min=settings["price_min"], price_max=settings["price_max"],
        )
    except Exception as exc:
        print(f"manual10: screener pass failed: {exc}")
        return []
    for p in picks:
        p["open_price"] = p["latest_price"]  # filled in properly at buy time by _finalize_screener_and_buy
        p["picked"] = final
    repo.save_candidates(trading_date, strategy_key, picks)
    return picks


def _finalize_screener_and_buy(trading_date: str, strategy_key: str, settings: Dict[str, Any]) -> None:
    picks = _run_screener_pass(trading_date, strategy_key, settings, final=True)
    if not picks:
        return
    client = _paper_client()
    buy_in = float(settings["buy_in_per_stock"])
    execute = paper_execute_enabled()
    for pick in picks:
        symbol = pick["symbol"]
        price = pick["latest_price"]
        if not price or price <= 0:
            continue
        shares = round(buy_in / price, 6)
        order_ok = True
        if execute:
            order = client.submit_market_order(symbol, shares, "buy")
            order_ok = order is not None
        if order_ok:
            repo.open_position(
                trading_date=trading_date, strategy_key=strategy_key, symbol=symbol, bucket="paper",
                shares=shares, entry_price=price,
            )


def _record_price_snapshots(trading_date: str, strategy_key: str) -> None:
    open_positions = repo.list_positions(trading_date, strategy_key, status="open")
    if not open_positions:
        return
    symbols = sorted({p["symbol"] for p in open_positions})
    paper_client = _paper_client()
    prices = paper_client.get_quotes(symbols)
    for symbol, price in prices.items():
        repo.record_price_snapshot(trading_date, strategy_key, symbol, price)


def _check_promotions(trading_date: str, strategy_key: str, settings: Dict[str, Any], now: datetime) -> None:
    window = timedelta(minutes=settings["promotion_window_minutes"])
    for position in repo.list_positions(trading_date, strategy_key, bucket="paper", status="open"):
        if position["promotion_checked"]:
            continue
        entry_time = datetime.fromisoformat(position["entry_time"])
        if now < entry_time + window:
            continue
        _attempt_promotion(position)


def _attempt_promotion(position: Dict[str, Any]) -> None:
    symbol = position["symbol"]
    paper_client = _paper_client()
    current_price = _current_price(symbol, paper_client)
    if current_price is None:
        repo.update_position(position["id"], promotion_checked=1, promotion_note="no quote available at check time")
        return

    grew = current_price > position["entry_price"]
    if not grew:
        repo.update_position(
            position["id"], promotion_checked=1,
            promotion_note=f"stayed in paper: {current_price:.2f} was not above entry {position['entry_price']:.2f}",
        )
        return

    if not (live_execute_enabled() and real_money_promotion_allowed()):
        repo.update_position(
            position["id"], promotion_checked=1,
            promotion_note=(
                "would have promoted (up from entry) but real-money execution is disabled -- "
                "set ALPACA_LIVE_EXECUTE and MANUAL10_ALLOW_REAL_MONEY to enable"
            ),
        )
        return

    live_client = _live_client()
    shares = round(position["shares"], 6)
    try:
        order = live_client.submit_market_order(symbol, shares, "buy")
    except Exception as exc:
        repo.update_position(position["id"], promotion_checked=1, promotion_note=f"promotion order failed: {exc}")
        return

    repo.close_position(position["id"], exit_price=current_price, close_reason="promoted")
    repo.update_position(position["id"], promotion_checked=1)
    repo.open_position(
        trading_date=position["trading_date"], strategy_key=position["strategy_key"], symbol=symbol, bucket="real",
        shares=shares, entry_price=current_price, real_order_id=order.order_id,
        promoted_from_paper=True,
    )


def _close_all_open_positions(trading_date: str, strategy_key: str, *, reason: str) -> None:
    paper_client = _paper_client()
    live_client = _live_client()
    for position in repo.list_positions(trading_date, strategy_key, status="open"):
        symbol = position["symbol"]
        bucket = position["bucket"]
        client = paper_client if bucket == "paper" else live_client
        current_price = _current_price(symbol, client)
        if current_price is None:
            continue  # try again next tick rather than closing at an unknown price

        should_execute = paper_execute_enabled() if bucket == "paper" else (
            live_execute_enabled() and real_money_promotion_allowed()
        )
        if should_execute:
            try:
                client.submit_market_order(symbol, position["shares"], "sell")
            except Exception as exc:
                print(f"manual10: {reason} sell failed for {symbol} ({bucket}): {exc}")
                continue
        repo.close_position(position["id"], exit_price=current_price, close_reason=reason)


class ManualActionError(ValueError):
    """A manual promote/demote/sell request that can't be carried out as
    asked -- the API layer maps this straight to a 400."""


def _require_open_position(position_id: int, expected_bucket: str) -> Dict[str, Any]:
    position = repo.get_position(position_id)
    if position is None:
        raise ManualActionError(f"no position {position_id}")
    if position["status"] != "open":
        raise ManualActionError(f"position {position_id} is already {position['status']}")
    if position["bucket"] != expected_bucket:
        raise ManualActionError(f"position {position_id} is in {position['bucket']}, not {expected_bucket}")
    return position


def manual_sell(position_id: int) -> Dict[str, Any]:
    """A human clicked Sell. Gated the same as every other manual live/paper
    trading control in this app -- just the account-wide execute switch for
    that bucket, no extra acknowledgment (that's only for the engine's own
    unattended decisions -- see the module docstring)."""
    position = repo.get_position(position_id)
    if position is None:
        raise ManualActionError(f"no position {position_id}")
    if position["status"] != "open":
        raise ManualActionError(f"position {position_id} is already {position['status']}")

    bucket = position["bucket"]
    client = _paper_client() if bucket == "paper" else _live_client()
    current_price = _current_price(position["symbol"], client)
    if current_price is None:
        raise ManualActionError(f"no live quote for {position['symbol']} right now")

    should_execute = paper_execute_enabled() if bucket == "paper" else live_execute_enabled()
    if should_execute:
        try:
            client.submit_market_order(position["symbol"], position["shares"], "sell")
        except Exception as exc:
            raise ManualActionError(f"sell order failed: {exc}") from exc
    repo.close_position(position_id, exit_price=current_price, close_reason="manual_sell")
    return repo.get_position(position_id)


def manual_promote(position_id: int) -> Dict[str, Any]:
    """A human clicked "Real Money" on a paper position. Requires
    ALPACA_LIVE_EXECUTE (the same bar every other manual live-trading control
    in this app clears) -- not the engine's extra MANUAL10_ALLOW_REAL_MONEY
    switch, which exists specifically to distinguish an unattended scheduler
    decision from a human clicking a button."""
    position = _require_open_position(position_id, "paper")
    if not live_execute_enabled():
        raise ManualActionError("real-money execution is disabled (ALPACA_LIVE_EXECUTE is not set)")

    live_client = _live_client()
    current_price = _current_price(position["symbol"], live_client)
    if current_price is None:
        raise ManualActionError(f"no live quote for {position['symbol']} right now")
    shares = round(position["shares"], 6)
    try:
        order = live_client.submit_market_order(position["symbol"], shares, "buy")
    except Exception as exc:
        raise ManualActionError(f"promotion order failed: {exc}") from exc

    repo.close_position(position_id, exit_price=current_price, close_reason="promoted")
    repo.update_position(position_id, promotion_checked=1)
    new_id = repo.open_position(
        trading_date=position["trading_date"], strategy_key=position["strategy_key"], symbol=position["symbol"],
        bucket="real", shares=shares, entry_price=current_price, real_order_id=order.order_id,
        promoted_from_paper=True,
    )
    return repo.get_position(new_id)


def manual_demote(position_id: int) -> Dict[str, Any]:
    """A human clicked "Paper Money" on a real position: sell it for real,
    then open the equivalent paper position at the same share count. Same
    gating rationale as manual_promote."""
    position = _require_open_position(position_id, "real")
    if not live_execute_enabled():
        raise ManualActionError("real-money execution is disabled (ALPACA_LIVE_EXECUTE is not set)")

    live_client = _live_client()
    current_price = _current_price(position["symbol"], live_client)
    if current_price is None:
        raise ManualActionError(f"no live quote for {position['symbol']} right now")
    try:
        live_client.submit_market_order(position["symbol"], position["shares"], "sell")
    except Exception as exc:
        raise ManualActionError(f"demotion sell order failed: {exc}") from exc

    repo.close_position(position_id, exit_price=current_price, close_reason="demoted")
    new_id = repo.open_position(
        trading_date=position["trading_date"], strategy_key=position["strategy_key"], symbol=position["symbol"],
        bucket="paper", shares=position["shares"], entry_price=current_price,
    )
    return repo.get_position(new_id)


def _finalize_day(trading_date: str, strategy_key: str, now: datetime) -> None:
    positions = repo.list_positions(trading_date, strategy_key)
    realized_pnl = sum(
        (p["exit_price"] - p["entry_price"]) * p["shares"]
        for p in positions
        if p.get("exit_price") is not None
    )
    result = "win" if realized_pnl > 0 else ("loss" if realized_pnl < 0 else "flat")
    repo.update_day(
        trading_date, strategy_key, phase="closed", realized_pnl=realized_pnl, result=result, closed_at=_iso(now),
    )
