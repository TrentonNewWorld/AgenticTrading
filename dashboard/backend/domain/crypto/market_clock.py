"""Trading-day boundary for the Crypto dashboard. Mirrors domain/futures/
market_clock.py and domain/forex/market_clock.py exactly, except crypto
trades genuinely 24/7/365 (confirmed in the 2026-08-23 spike: 730 daily
bars over 2 years for BTC/USD with zero gaps, not even weekends) -- so
"trading day" here isn't even an approximation of a real closure window
the way it loosely is for futures/forex, it's simply the US/Eastern
calendar date used as a display/grouping boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

_ET = None


def _et_zone():
    global _ET
    if _ET is None:
        from zoneinfo import ZoneInfo

        _ET = ZoneInfo("America/New_York")
    return _ET


@dataclass(frozen=True)
class TodaySession:
    trading_date: date
    has_session: bool
    open_at: Optional[datetime]
    close_at: Optional[datetime]
    now: datetime


def get_today_session(today: Optional[date] = None) -> TodaySession:
    now_utc = datetime.now(timezone.utc)
    trading_date = today or now_utc.astimezone(_et_zone()).date()
    open_at = datetime(trading_date.year, trading_date.month, trading_date.day, tzinfo=_et_zone()).astimezone(timezone.utc)
    close_at = open_at + timedelta(days=1)
    return TodaySession(trading_date=trading_date, has_session=True, open_at=open_at, close_at=close_at, now=now_utc)
