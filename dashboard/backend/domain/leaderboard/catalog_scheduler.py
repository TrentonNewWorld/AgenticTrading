"""Background daemon that runs every activated Strategy Catalog strategy
once per real trading day -- what makes catalog_activation.py's "activated"
flag actually mean something ongoing, rather than a stored bit nothing ever
reads.

Same idempotent-start pattern as domain/manual10/scheduler.py (module-global
thread handle guarded by a lock, daemon=True, strict opt-in via an env var
read fresh on every call). The difference from every other scheduler in this
repo: this one drives REAL and paper-real Alpaca *accounts* (not a locally
simulated wallet), so a poll interval far shorter than "once a day" would be
pointless -- the actual pacing is ``last_run_trading_date`` in
catalog_activation.py, checked on every poll so a short poll interval only
costs a redundant DB read, never a redundant order.

Activating a strategy does NOT by itself arm real-money execution: every
tick here calls run_paper_for_strategy/run_live_for_strategy with
dry_run=False, but those functions still require their own separate
ALPACA_PAPER_EXECUTE / ALPACA_LIVE_EXECUTE env var to actually place an
order (see execution/alpaca_live_service.py's should_execute check) -- the
same two-gate pattern (activation != execution) already used everywhere
else real money is involved in this repo. An activated strategy with
neither EXECUTE flag armed just runs a daily *dry-run* review, harmlessly,
forever, until the operator explicitly arms execution too.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import date
from typing import Optional

from dashboard.backend.domain.leaderboard import catalog_activation
from dashboard.backend.domain.manual10.market_clock import today_trading_date

DEFAULT_INTERVAL_SECONDS = 300.0

_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def scheduler_enabled() -> bool:
    return os.getenv("STRATEGY_CATALOG_SCHEDULER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _run_mode(mode: str, trading_date: str) -> None:
    from dashboard.backend.execution import alpaca_live_service, alpaca_paper_service

    runner = alpaca_paper_service.run_paper_for_strategy if mode == "paper" else alpaca_live_service.run_live_for_strategy

    for row in catalog_activation.list_activated(mode):
        if row["last_run_trading_date"] == trading_date:
            continue  # already ran today -- see this module's docstring
        strategy_key = row["strategy_key"]
        try:
            result = asyncio.run(
                runner(strategy_key=strategy_key, symbols=None, dry_run=False, user_id=row["user_id"])
            )
            status = result.get("status", "completed")
        except Exception as exc:
            status = f"error: {exc}"
            print(f"strategy-catalog scheduler: {mode} tick failed for {strategy_key}: {exc}")
        catalog_activation.record_tick(strategy_key, mode, trading_date, status)


def _is_probably_a_trading_day(d: date) -> bool:
    """Monday-Friday, no holiday calendar -- deliberately network-free, same
    reasoning as manual10/market_clock.py::today_trading_date() (which this
    function's caller uses for the date itself): this scheduler must not be
    blocked entirely just to answer "is the market plausibly open today," a
    question that doesn't need Alpaca's live calendar for the ~99% of days
    that aren't one of the nine NYSE holidays a year. The earlier version
    called manual10/market_clock.py's get_today_session() here -- which
    does hit Alpaca's live calendar with the PAPER credentials -- so an
    invalid/expired paper key silently blocked EVERY activated strategy's
    tick, including live-mode ones with nothing to do with paper trading
    (caught live: a user's activated live strategy never ran at all because
    of this). The cost of the imprecision here is at most nine harmless
    no-op tick attempts a year on an actual holiday morning (_run_mode's own
    calls would just fail/no-op against a closed market) -- far better than
    silently never running, every day, whenever the paper key is stale."""
    return d.weekday() < 5


def tick() -> None:
    trading_date_obj = today_trading_date()
    if not _is_probably_a_trading_day(trading_date_obj):
        return
    trading_date = str(trading_date_obj)
    _run_mode("paper", trading_date)
    _run_mode("live", trading_date)


def start_scheduler(interval_seconds: Optional[float] = None) -> bool:
    if not scheduler_enabled():
        return False

    global _thread
    interval = interval_seconds if interval_seconds is not None else DEFAULT_INTERVAL_SECONDS
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True

        def _loop() -> None:
            while True:
                try:
                    tick()
                except Exception as exc:
                    print(f"strategy-catalog scheduler: tick failed: {exc}")
                time.sleep(interval)

        _thread = threading.Thread(target=_loop, daemon=True, name="strategy-catalog-scheduler")
        _thread.start()
    return True


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()
