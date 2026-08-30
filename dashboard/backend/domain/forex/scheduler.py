"""Background daemon that calls engine.tick() on an interval -- mirrors
domain/futures/scheduler.py exactly (see that module's docstring for why
this was missing and what it fixes).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

from dashboard.backend.domain.forex import engine

DEFAULT_INTERVAL_SECONDS = 30.0

_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def scheduler_enabled() -> bool:
    return os.getenv("FOREX_SCHEDULER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


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
                    engine.tick()
                except Exception as exc:
                    print(f"forex: scheduler tick failed: {exc}")
                time.sleep(interval)

        _thread = threading.Thread(target=_loop, daemon=True, name="forex-scheduler")
        _thread.start()
    return True


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()
