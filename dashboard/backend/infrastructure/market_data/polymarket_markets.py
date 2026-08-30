"""Polymarket public market data -- no authentication needed for any of this.

Verified live against the real Polymarket Gamma API this session
(2026-08-24; no credentials were used, this is a public GET endpoint):

* Base URL ``https://gamma-api.polymarket.com`` answers unauthenticated
  ``GET /markets`` with real current markets -- e.g. a live query returned
  "Xi Jinping out before 2027?" with ``outcomes: ["Yes", "No"]``,
  ``outcomePrices: ["0.0485", "0.9515"]``, real volume figures. Unlike
  Kalshi's default feed, Gamma's isn't dominated by auto-generated
  combination markets -- ordinary single-question markets are the norm here,
  no prefix filtering needed.
* ``outcomePrices`` (JSON-encoded string arrays, parsed below) already give
  a current probability-as-price per outcome directly from this endpoint --
  no separate CLOB order-book call is needed just to read a price. The CLOB
  API (``https://clob.polymarket.com``, also unauthenticated for reads)
  exists for finer-grained bid/ask/depth if a later Prediction feature needs
  it, but isn't used here.

This module never needs credentials -- Polymarket has no demo/sandbox mode
at all (unlike Kalshi), so every Prediction strategy's paper trading is
simulated against this same real public data regardless of whether a wallet
key is connected. See ``infrastructure/brokers/polymarket_paper.py``'s
docstring: a connected key only ever enables real-money order placement.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests

POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


class MarketDataUnavailableError(RuntimeError):
    """Raised when Polymarket's public market data can't be fetched --
    callers treat this as "nothing to trade right now," not a hard error,
    matching every other market-data module's posture in this repo."""


def _parse_outcomes(market: Dict[str, Any]) -> Dict[str, float]:
    """{"Yes": 0.0485, "No": 0.9515} from the raw JSON-string-encoded
    ``outcomes``/``outcomePrices`` fields Gamma returns. Empty on any
    malformed/missing pair rather than raising -- one bad market must not
    take down a whole listing."""
    try:
        outcomes = json.loads(market.get("outcomes") or "[]")
        prices = json.loads(market.get("outcomePrices") or "[]")
    except (TypeError, ValueError):
        return {}
    if len(outcomes) != len(prices):
        return {}
    try:
        return {str(name): float(price) for name, price in zip(outcomes, prices)}
    except (TypeError, ValueError):
        return {}


def list_active_markets(*, limit: int = 20) -> List[Dict[str, Any]]:
    """Currently-open markets. Each item carries the raw Gamma fields
    (``question``, ``conditionId``, ``slug``, ``volume``, ...) plus a parsed
    ``outcome_prices`` dict this module adds (see ``_parse_outcomes``)."""
    params = {"closed": "false", "limit": max(1, min(limit, 500))}
    try:
        response = requests.get(f"{POLYMARKET_GAMMA_BASE_URL}/markets", params=params, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MarketDataUnavailableError(f"Polymarket markets request failed: {exc}") from exc

    markets = response.json()
    if not isinstance(markets, list):
        return []
    for market in markets:
        market["outcome_prices"] = _parse_outcomes(market)
    return markets


def get_market(condition_id: str) -> Optional[Dict[str, Any]]:
    """A single market's current state, by conditionId."""
    try:
        response = requests.get(
            f"{POLYMARKET_GAMMA_BASE_URL}/markets", params={"condition_ids": condition_id}, timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MarketDataUnavailableError(f"Polymarket market {condition_id!r} request failed: {exc}") from exc
    markets = response.json()
    if not isinstance(markets, list) or not markets:
        return None
    market = markets[0]
    market["outcome_prices"] = _parse_outcomes(market)
    return market
