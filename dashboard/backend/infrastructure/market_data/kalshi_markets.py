"""Kalshi public market data -- no authentication needed for any of this.

Verified live against the real Kalshi API this session (2026-08-24; no
credentials were used, these are all public GET endpoints):

* Base URL ``https://api.elections.kalshi.com/trade-api/v2`` answers
  unauthenticated ``GET /events`` and ``GET /markets`` (~30 req/s public rate
  limit per Kalshi's own docs) with real current data.
* The response schema uses ``*_dollars``-suffixed decimal-string fields
  (``yes_bid_dollars``, ``yes_ask_dollars``, ``no_bid_dollars``,
  ``no_ask_dollars``, ``last_price_dollars``), **not** the older
  cents-integer fields (``yes_bid``, ``yes_ask``, ...) that older
  third-party writeups describe -- this repo's own client uses the verified
  field names, not the commonly-cited ones.
* ``GET /markets?status=open`` alone is a bad source for a market universe
  right now: its default ordering is dominated by thousands of Kalshi's
  auto-generated ``KXMVECROSSCATEGORY-*`` "multivariate" combination
  (parlay-style) tickers, to the point that even a 1000-row page returned
  zero ordinary single-question markets in this session's testing.
  ``GET /events?status=open&with_nested_markets=true`` does not have this
  problem -- a live query returned genuine markets like "Who will the next
  Pope be?", "Will the world pass 2 degrees Celsius...", each with its own
  nested ``markets`` array carrying real ``yes_bid_dollars``/
  ``yes_ask_dollars`` -- so this module reads events, not markets, directly.
"""

from __future__ import annotations

from typing import Any, Dict, List

import requests

KALSHI_PUBLIC_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

#: Kalshi's own auto-generated combinatorial event tickers -- excluded on
#: the rare chance one slips into the events feed too. See module docstring.
_EXCLUDED_TICKER_PREFIX = "KXMVECROSSCATEGORY"


class MarketDataUnavailableError(RuntimeError):
    """Raised when Kalshi's public market data can't be fetched -- callers
    treat this as "nothing to trade right now," not a hard error, matching
    every other market-data module's posture in this repo."""


def list_active_events(*, limit: int = 20) -> List[Dict[str, Any]]:
    """Currently-open events, each carrying its own nested ``markets`` list
    (real single-question markets, each with ``ticker``, ``yes_bid_dollars``,
    ``yes_ask_dollars``, ``no_bid_dollars``, ``no_ask_dollars``,
    ``last_price_dollars``, ``close_time``, ``volume_fp`` -- the verified
    real field names). An event with more than one candidate outcome (e.g.
    "Who will be the next NATO Secretary General?") carries one market per
    candidate."""
    params = {"status": "open", "limit": max(1, min(limit, 200)), "with_nested_markets": "true"}
    try:
        response = requests.get(f"{KALSHI_PUBLIC_BASE_URL}/events", params=params, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MarketDataUnavailableError(f"Kalshi events request failed: {exc}") from exc

    events = response.json().get("events", [])
    return [e for e in events if not str(e.get("event_ticker", "")).startswith(_EXCLUDED_TICKER_PREFIX)]


def list_active_markets(*, limit: int = 20) -> List[Dict[str, Any]]:
    """Flattened markets across ``list_active_events`` -- one entry per
    tradeable outcome, each with an ``event_title`` field added for display."""
    out: List[Dict[str, Any]] = []
    for event in list_active_events(limit=limit):
        for market in event.get("markets", []):
            market["event_title"] = event.get("title")
            out.append(market)
    return out


def get_market(ticker: str) -> Dict[str, Any]:
    """A single market's current state, by ticker."""
    try:
        response = requests.get(f"{KALSHI_PUBLIC_BASE_URL}/markets/{ticker}", timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MarketDataUnavailableError(f"Kalshi market {ticker!r} request failed: {exc}") from exc
    return response.json().get("market", {})
