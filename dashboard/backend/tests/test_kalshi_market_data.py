"""Mocked-HTTP tests for infrastructure/market_data/kalshi_markets.py.

The endpoint shape and field names here were verified live against the real
Kalshi API this session (see that module's docstring) -- these tests pin
that verified shape against regressions, not re-verify it against the network.
"""

from __future__ import annotations

import pytest

from dashboard.backend.infrastructure.market_data.kalshi_markets import (
    KALSHI_PUBLIC_BASE_URL,
    MarketDataUnavailableError,
    get_market,
    list_active_events,
    list_active_markets,
)


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


_SAMPLE_EVENTS = {
    "events": [
        {
            "event_ticker": "KXNEWPOPE-70",
            "title": "Who will the next Pope be?",
            "markets": [
                {"ticker": "KXNEWPOPE-70-PPIZ", "yes_bid_dollars": "0.0300", "yes_ask_dollars": "0.0450"},
                {"ticker": "KXNEWPOPE-70-PTAG", "yes_bid_dollars": "0.0100", "yes_ask_dollars": "0.0200"},
            ],
        },
        {
            "event_ticker": "KXMVECROSSCATEGORY-SHARD1-ABC",
            "title": "auto-generated parlay noise",
            "markets": [{"ticker": "KXMVECROSSCATEGORY-SHARD1-ABC-1"}],
        },
    ]
}


def test_list_active_events_excludes_multivariate_combination_noise(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _FakeResponse(_SAMPLE_EVENTS)

    monkeypatch.setattr("dashboard.backend.infrastructure.market_data.kalshi_markets.requests.get", fake_get)

    events = list_active_events(limit=10)
    assert len(events) == 1
    assert events[0]["event_ticker"] == "KXNEWPOPE-70"
    assert calls[0][0] == f"{KALSHI_PUBLIC_BASE_URL}/events"
    assert calls[0][1] == {"status": "open", "limit": 10, "with_nested_markets": "true"}


def test_list_active_markets_flattens_events_with_event_title(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.kalshi_markets.requests.get",
        lambda *a, **k: _FakeResponse(_SAMPLE_EVENTS),
    )
    markets = list_active_markets(limit=10)
    assert len(markets) == 2  # both real markets under the one non-excluded event
    assert all(m["event_title"] == "Who will the next Pope be?" for m in markets)
    assert {m["ticker"] for m in markets} == {"KXNEWPOPE-70-PPIZ", "KXNEWPOPE-70-PTAG"}


def test_get_market_returns_single_market(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.kalshi_markets.requests.get",
        lambda *a, **k: _FakeResponse({"market": {"ticker": "KXFOO-1", "yes_bid_dollars": "0.5000"}}),
    )
    market = get_market("KXFOO-1")
    assert market["ticker"] == "KXFOO-1"


def test_network_failure_raises_unavailable(monkeypatch):
    import requests

    def fake_get(*a, **k):
        raise requests.RequestException("timeout")

    monkeypatch.setattr("dashboard.backend.infrastructure.market_data.kalshi_markets.requests.get", fake_get)
    with pytest.raises(MarketDataUnavailableError):
        list_active_events()
