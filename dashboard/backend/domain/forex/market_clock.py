"""Trading-day boundary for the Forex dashboard. Mirrors domain/futures/
market_clock.py exactly -- forex trades nearly 24/5 (Sunday 5pm ET through
Friday 5pm ET) just like CME futures do, so the same reasoning applies: no
single NYSE-style open/close to key an opening-range screener off (which is
why Forex Manual is upload-only too), and "trading day" is simply the
US/Eastern calendar date treated as fully in-session.
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
