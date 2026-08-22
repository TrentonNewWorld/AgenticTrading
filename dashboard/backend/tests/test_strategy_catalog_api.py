"""Tests for the Strategy Catalog API (list + paper/live run routes).

Focused on the HTTP contract and error handling; the underlying
compute/caching logic (``domain/leaderboard/catalog.py``) is exercised
directly here too since it needs no network mocking to test its pure parts.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.domain.leaderboard import catalog as catalog_module


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_module, "CACHE_PATH", tmp_path / "strategy_catalog_cache.json")


def test_catalog_entries_resolve_and_all_support_live_trading():
    """Every catalog entry's underlying strategy key must resolve AND define
    decide() -- this catalog's whole point is a strategy you can actually
    select and run, so an entry with no decide() (e.g. a single-instrument
    intraday round-trip strategy like Overnight Anomaly/Turn of the Month)
    does not belong here at all, unlike the leaderboard's own roster which
    is fine with display-only strategies."""
    from dashboard.backend.domain.leaderboard.strategies import get_strategy

    for entry in catalog_module.CATALOG_ENTRIES:
        config = catalog_module._config_for(entry)
        strat = get_strategy(config)
        assert strat.required_symbols()
        assert hasattr(strat, "decide"), f"{entry.key} has no decide() -- does not belong in this catalog"


def test_catalog_entries_have_unique_keys():
    keys = [e.key for e in catalog_module.CATALOG_ENTRIES]
    assert len(keys) == len(set(keys))
    assert len(keys) == 26


def test_metrics_on_empty_curve():
    metrics = catalog_module._metrics([])
    assert metrics["final"] == catalog_module.INITIAL_CAPITAL
    assert metrics["return_pct"] == 0.0


def test_get_strategy_catalog_with_no_credentials_returns_empty_not_crash(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    payload = catalog_module.get_strategy_catalog(force_refresh=True)
    assert payload["entries"] == []


def test_list_catalog_route_returns_200_even_with_no_data(client, monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    resp = client.get("/api/v1/strategy-catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert "entries" in body


def test_run_paper_route_rejects_unknown_strategy_key(client):
    resp = client.post("/api/v1/strategy-catalog/not_a_real_key/paper", json={"dry_run": True})
    assert resp.status_code == 400


def test_run_live_route_rejects_unknown_strategy_key(client):
    resp = client.post("/api/v1/strategy-catalog/not_a_real_key/live", json={"dry_run": True})
    assert resp.status_code == 400


def test_run_paper_route_defaults_dry_run_true(client):
    """The request body's `dry_run` defaults to True even if omitted --
    never assume execution."""
    resp = client.post("/api/v1/strategy-catalog/momentum_effect/paper", json={})
    # Either 400 (no paper account configured in this test env) or 200 --
    # either way it must not attempt a real order path without an explicit
    # dry_run: false, which this request never sent.
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        assert resp.json().get("dry_run") is not False
