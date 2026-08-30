"""Background queue processor for the Testing page.

Same idiom as ``domain.manual10.scheduler`` (a daemon thread, idempotent
start, guarded by a module-level lock) but always on rather than opt-in:
unlike Manual's trading scheduler, this thread never touches a broker or
real money -- it only scans and backtests already-sandboxed code -- so there
is no safety reason to make it opt-in, and making it opt-in would mean the
feature silently does nothing on a fresh install.

Processes exactly one queue item at a time (never overlapping two scans/
backtests), so a user who queues several uploads gets them worked through in
submission order -- and because state lives in SQLite (``repository.py``),
navigating away from the Testing page and back never loses progress.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from dashboard.backend.domain.strategy_testing import backtester, repository as repo, scanner

DEFAULT_POLL_SECONDS = 3.0

_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def _process_one() -> bool:
    """Pick up and fully process the oldest queued item, if any (one shared
    FIFO queue across every asset class -- see next_queued()'s own
    docstring). Returns True if an item was processed (so the caller can
    poll faster right after), False if the queue was empty."""
    item = repo.next_queued()
    if not item:
        return False

    row_id = item["id"]
    code = item["code"]
    asset_class = item.get("asset_class") or "stocks"
    user_id = item.get("user_id")

    try:
        repo.mark_scanning(row_id)
        scan_result = scanner.scan(code, asset_class, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 -- a scanner crash must not wedge the queue
        repo.mark_error(row_id, error=f"scan failed: {exc}")
        return True

    if not scan_result["accepted"]:
        repo.mark_rejected(
            row_id,
            scan_verdict=scan_result["verdict"],
            scan_notes=scan_result["notes"],
            scan_is_strategy=scan_result["is_strategy"],
        )
        return True

    repo.mark_backtesting(
        row_id,
        scan_verdict=scan_result["verdict"],
        scan_notes=scan_result["notes"],
        scan_is_strategy=scan_result["is_strategy"],
    )
    try:
        if asset_class == "options":
            result = backtester.run_options_backtest(code)
        elif asset_class == "futures":
            result = backtester.run_futures_backtest(code)
        elif asset_class == "forex":
            result = backtester.run_forex_backtest(code)
        elif asset_class == "crypto":
            result = backtester.run_crypto_backtest(code)
        else:
            result = backtester.run_backtest(code)
    except Exception as exc:  # noqa: BLE001 -- a backtest crash must not wedge the queue
        repo.mark_error(row_id, error=str(exc))
        return True

    repo.mark_ready(row_id, result=result)
    return True


def start_worker(poll_seconds: float = DEFAULT_POLL_SECONDS) -> bool:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True  # idempotent no-op

        def _loop() -> None:
            while True:
                try:
                    processed = _process_one()
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive no matter what
                    print(f"strategy_testing: worker tick failed: {exc}")
                    processed = False
                time.sleep(0.5 if processed else poll_seconds)

        _thread = threading.Thread(target=_loop, daemon=True, name="strategy-testing-worker")
        _thread.start()
    return True


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()
