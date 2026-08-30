"""Background daemon that calls engine.tick() on an interval -- mirrors the
run-reaper's idempotent-start pattern (domain/runs/service.py::start_reaper)
exactly: a module-global thread handle guarded by a lock, a no-op if it's
already alive, daemon=True so it never blocks process shutdown.

Strict opt-in, unlike the reaper (which has no off switch): the reaper only
ever cleans up already-decided state, but this scheduler is what makes Manual
10 buy and sell stocks on its own, every trading day, with no per-action human
click. Same convention as LEADERBOARD_DAILY_AUTO_DEPLOY elsewhere in this
codebase -- read fresh on every call (not cached at import time) so a config
change takes effect without a redeploy, and default OFF so cloning this repo,
running the test suite, or a bare `uvicorn` invocation never starts an
autonomous trading loop by accident.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

from dashboard.backend.domain.manual10 import engine

#: How often the daemon calls tick() while running. Independent of any of the
#: engine's own minute-scale windows (screener/promotion/close-out) -- this
#: only needs to be short enough that those windows are observed promptly.
DEFAULT_INTERVAL_SECONDS = 20.0

_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def scheduler_enabled() -> bool:
    return os.getenv("MANUAL10_SCHEDULER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def start_scheduler(interval_seconds: Optional[float] = None) -> bool:
    """Start the background tick loop if MANUAL10_SCHEDULER_ENABLED is set
    (idempotent -- a second call no-ops). Returns whether it's running after
    the call, so the app's startup log can say plainly whether this deploy
    can trade for real."""
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
                    engine.tick()
                except Exception as exc:
                    print(f"manual10: scheduler tick failed: {exc}")
                time.sleep(interval)

        _thread = threading.Thread(target=_loop, daemon=True, name="manual10-scheduler")
        _thread.start()
    return True


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()
