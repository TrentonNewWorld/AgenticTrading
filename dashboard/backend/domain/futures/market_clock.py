"""Trading-day boundary for the Futures dashboard.

CME futures trade nearly 24/5 (Sunday 6pm ET through Friday 5pm ET, with a
brief daily maintenance break) -- there is no NYSE-style single open/close
window to key an "opening range" screener off, which is exactly why the
Futures Manual page is upload-only per the user's own decision rather than a
Top-10-style screener (see domain/futures/engine.py's module docstring).

Deliberately does NOT call Alpaca's /calendar the way domain/manual10/
market_clock.py does -- Alpaca has no futures calendar, and this dashboard's
default posture is fully simulated on free Yahoo Finance data (see
infrastructure/market_data/yahoo_futures.py), so requiring Alpaca credentials
just to know "what day is it" would be a needless dependency. "Trading day"
here is simply the US/Eastern calendar date, with the whole day treated as
in-session -- exact CME maintenance-break/weekend-closure precision isn't
worth modeling for a simulated dashboard, and a real Tradovate connection
(once wired, see infrastructure/brokers/tradovate_paper.py) would reject an
order Yahoo's simulated fill priced during an actual closure anyway.
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
