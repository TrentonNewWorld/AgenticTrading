"""Background daemon that advances every waiting Prediction strategy once
per real calendar day. Same idiom as domain/strategy_testing/worker.py (a
daemon thread, idempotent start, guarded by a module-level lock) and, like
that worker and unlike domain/manual10/scheduler.py, always on rather than
opt-in: this loop only ever produces simulated paper fills against public
market data (domain/prediction/engine.py never places a real order -- that
would require a user's own connected Kalshi/Polymarket credentials AND a
real-execution path that doesn't exist yet), so there is no real-money
reason to gate it, and gating it would mean the 5-day clock silently never
advances on a fresh install.

Wakes on a short interval (not once precisely at midnight) and relies on
engine.tick_all()'s own per-day idempotency (repository.list_due_for_tick
only returns strategies not yet ticked for "today") to make repeated wakeups
within the same day a cheap no-op -- simpler and more robust than trying to
land exactly on a day boundary across process restarts/redeploys.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from dashboard.backend.domain.prediction import engine

#: Frequent enough that a strategy submitted any time during the day is
#: ticked well before that day ends, without being wasteful -- engine.tick_all
#: is a no-op within seconds once today's due strategies are all ticked.
DEFAULT_INTERVAL_SECONDS = 1800.0  # 30 minutes

_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def start_scheduler(interval_seconds: Optional[float] = None) -> bool:
    """Start the background tick loop (idempotent -- a second call no-ops).
    Returns whether it's running after the call."""
    global _thread
    interval = interval_seconds if interval_seconds is not None else DEFAULT_INTERVAL_SECONDS
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True

        def _loop() -> None:
            while True:
                try:
                    engine.tick_all()
                except Exception as exc:  # noqa: BLE001 -- keep the loop alive no matter what
                    print(f"prediction: scheduler tick failed: {exc}")
                time.sleep(interval)

        _thread = threading.Thread(target=_loop, daemon=True, name="prediction-scheduler")
        _thread.start()
    return True


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()
