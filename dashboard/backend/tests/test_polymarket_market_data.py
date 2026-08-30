"""Mocked-HTTP tests for infrastructure/market_data/polymarket_markets.py.

The endpoint shape and field names here were verified live against the real
Polymarket Gamma API this session (see that module's docstring) -- these
tests pin that verified shape against regressions, not re-verify it against
the network.
"""

from __future__ import annotations

import json

import pytest

from dashboard.backend.infrastructure.market_data.polymarket_markets import (
    MarketDataUnavailableError,
    get_market,
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


_SAMPLE_MARKET = {
    "id": "559651",
    "question": "Xi Jinping out before 2027?",
    "conditionId": "0xabc123",
    "outcomes": json.dumps(["Yes", "No"]),
    "outcomePrices": json.dumps(["0.0485", "0.9515"]),
    "volume": 12580650.49,
    "active": True,
    "closed": False,
}


def test_list_active_markets_parses_outcome_prices(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.polymarket_markets.requests.get",
        lambda *a, **k: _FakeResponse([_SAMPLE_MARKET]),
    )
    markets = list_active_markets(limit=5)
    assert len(markets) == 1
    assert markets[0]["outcome_prices"] == {"Yes": pytest.approx(0.0485), "No": pytest.approx(0.9515)}
    assert markets[0]["question"] == "Xi Jinping out before 2027?"


def test_malformed_outcomes_degrade_to_empty_dict_not_a_crash(monkeypatch):
    broken = dict(_SAMPLE_MARKET, outcomes="not json", outcomePrices=json.dumps(["0.5"]))
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.polymarket_markets.requests.get",
        lambda *a, **k: _FakeResponse([broken]),
    )
    markets = list_active_markets(limit=5)
    assert markets[0]["outcome_prices"] == {}


def test_get_market_returns_none_when_not_found(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.polymarket_markets.requests.get",
        lambda *a, **k: _FakeResponse([]),
    )
    assert get_market("nonexistent") is None


def test_get_market_returns_parsed_market(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.polymarket_markets.requests.get",
        lambda *a, **k: _FakeResponse([_SAMPLE_MARKET]),
    )
    market = get_market("0xabc123")
    assert market["outcome_prices"]["Yes"] == pytest.approx(0.0485)


def test_network_failure_raises_unavailable(monkeypatch):
    import requests

    def fake_get(*a, **k):
        raise requests.RequestException("timeout")

    monkeypatch.setattr("dashboard.backend.infrastructure.market_data.polymarket_markets.requests.get", fake_get)
    with pytest.raises(MarketDataUnavailableError):
        list_active_markets()
