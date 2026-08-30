"""Live wall-clock market-hours info, via Alpaca's own /clock and /calendar
endpoints -- not the historical-bar-timestamp helpers in
``domain/leaderboard/strategies/_common.py`` (those filter already-fetched bar
data, not "what time is it right now"). Nothing else in this codebase has a
live equivalent (confirmed by a full-repo grep before writing this), and
hand-rolling ET arithmetic would need our own NYSE holiday/half-day calendar,
which Alpaca's /calendar already provides authoritatively.

Uses the *paper* trading credentials -- the calendar/clock are account-agnostic
market facts, identical for paper and live, so there is no reason to require
live credentials just to know what time the market opens.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetCalendarRequest

_ET = None  # lazy -- zoneinfo import is cheap but avoid it at module import time in case tzdata is missing


def _et_zone():
    global _ET
    if _ET is None:
        from zoneinfo import ZoneInfo

        _ET = ZoneInfo("America/New_York")
    return _ET


@dataclass(frozen=True)
class TodaySession:
    trading_date: date
    has_session: bool  # False on a weekend/holiday -- open/close are None then
    open_at: Optional[datetime]  # tz-aware, UTC
    close_at: Optional[datetime]  # tz-aware, UTC
    now: datetime  # tz-aware, UTC

    @property
    def minutes_since_open(self) -> Optional[float]:
        if not self.has_session or self.now < self.open_at:
            return None
        return (self.now - self.open_at).total_seconds() / 60.0

    @property
    def minutes_until_close(self) -> Optional[float]:
        if not self.has_session or self.now >= self.close_at:
            return None
        return (self.close_at - self.now).total_seconds() / 60.0

    @property
    def is_open_now(self) -> bool:
        if not self.has_session:
            return False
        return self.open_at <= self.now < self.close_at


def _client() -> TradingClient:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY/ALPACA_SECRET_KEY must be set to read the market calendar")
    return TradingClient(api_key, secret_key, paper=True)


def today_trading_date() -> date:
    """Today's calendar date in US/Eastern -- "today" for a market calendar
    always means the exchange's own local date, not the server's UTC date.
    Pure local computation, no Alpaca call: callers that only need a date to
    key repository lookups by (not live open/close/in-session state) should
    use this instead of ``get_today_session()``, so a broken or expired
    Alpaca credential can't take down read-only listing/status endpoints
    that have nothing to do with live market hours."""
    return datetime.now(timezone.utc).astimezone(_et_zone()).date()


def get_today_session(today: Optional[date] = None) -> TodaySession:
    """The regular session's open/close for `today` (default: the real
    calendar date in US/Eastern, since "today" for a market calendar always
    means the exchange's own local date, not the server's UTC date), and
    whether the market is in session right now. `has_session=False` on a
    weekend or NYSE holiday -- no half-day/holiday logic of our own; Alpaca's
    calendar is the source of truth.

    Needs live Alpaca calendar access -- use ``today_trading_date()`` instead
    when a caller only needs the date, not open/close/in-session state (see
    that function's docstring for why the split exists)."""
    now_utc = datetime.now(timezone.utc)
    trading_date = today or now_utc.astimezone(_et_zone()).date()

    client = _client()
    calendar = client.get_calendar(GetCalendarRequest(start=trading_date, end=trading_date))
    if not calendar:
        return TodaySession(trading_date=trading_date, has_session=False, open_at=None, close_at=None, now=now_utc)

    session = calendar[0]
    open_at = session.open if session.open.tzinfo else session.open.replace(tzinfo=_et_zone())
    close_at = session.close if session.close.tzinfo else session.close.replace(tzinfo=_et_zone())
    return TodaySession(
        trading_date=trading_date,
        has_session=True,
        open_at=open_at.astimezone(timezone.utc),
        close_at=close_at.astimezone(timezone.utc),
        now=now_utc,
    )
