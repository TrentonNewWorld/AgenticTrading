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

    for entry in catalog_module._catalog_roster():
        config = catalog_module._config_for(entry)
        strat = get_strategy(config)
        assert strat.required_symbols()
        assert hasattr(strat, "decide"), f"{entry.key} has no decide() -- does not belong in this catalog"


def test_catalog_entries_have_unique_keys():
    keys = [e.key for e in catalog_module._catalog_roster()]
    assert len(keys) == len(set(keys))
    config = catalog_module.load_leaderboard_config()
    expected = [s for s in config["strategies"] if s.get("label") != "Model"]
    assert len(keys) == len(expected), (
        "every non-Model leaderboard.json entry must resolve into the catalog "
        "roster (or _catalog_roster's skip-on-failure is silently dropping one)"
    )


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


@pytest.mark.parametrize("key,expected_strategy", [
    ("equal_weight_djia", "equal_weight_index"),
    ("spy_index", "market_index"),
    ("djia_index", "market_index"),
    # mean_variance_djia was cut from the roster in the 2026-08 curation pass.
])
def test_resolve_run_config_maps_catalog_id_to_its_registry_strategy(key, expected_strategy):
    """Regression: a real user hit this live, trying to run equal_weight_djia
    for real money -- execution/alpaca_paper_service.py and
    alpaca_live_service.py used to build {"strategy": strategy_key, ...}
    directly from the URL's catalog key, which only works when a catalog
    entry's id happens to equal its registry strategy type. It doesn't for
    these four (leaderboard.json gives each a distinct display id), so every
    Run in Paper/Run in Live for them 500'd with "Unknown baseline strategy".
    resolve_run_config() is the fix: it goes through the same
    _catalog_roster()/_config_for() resolution the catalog's own backtest
    preview already used correctly."""
    from dashboard.backend.domain.leaderboard.strategies import get_strategy

    config = catalog_module.resolve_run_config(key)
    assert config["strategy"] == expected_strategy
    strat = get_strategy(config)
    assert hasattr(strat, "decide")


def test_resolve_run_config_spy_and_djia_index_get_distinct_symbols():
    """Both map to the same market_index strategy class -- only their
    _TRADEABLE_OVERRIDES symbols distinguish which single index they trade.
    A resolution that ignored that override would construct a working
    strategy for the wrong instrument, not a visible error."""
    spy = catalog_module.resolve_run_config("spy_index")
    djia = catalog_module.resolve_run_config("djia_index")
    assert spy["symbols"] == ["SPY"]
    assert djia["symbols"] == ["DIA"]


def test_run_paper_and_live_routes_no_longer_reject_equal_weight_djia(client):
    """HTTP-level pin of the same regression: whatever this returns in a
    test env with no real Alpaca credentials, it must not be the
    "Unknown baseline strategy" 400 the raw catalog-id-as-registry-key bug
    produced."""
    for path in ("paper", "live"):
        resp = client.post(f"/api/v1/strategy-catalog/equal_weight_djia/{path}", json={"dry_run": True})
        assert "Unknown baseline strategy" not in resp.text, (path, resp.text)


def test_activation_defaults_to_inactive(client):
    resp = client.get("/api/v1/strategy-catalog/momentum_effect/activation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["paper"]["activated"] is False
    assert body["live"]["activated"] is False


def test_activate_and_deactivate_paper(client):
    resp = client.post("/api/v1/strategy-catalog/momentum_effect/paper/activate")
    assert resp.status_code == 200
    assert resp.json()["activated"] is True

    listed = client.get("/api/v1/strategy-catalog/momentum_effect/activation").json()
    assert listed["paper"]["activated"] is True
    assert listed["live"]["activated"] is False  # independent of paper

    resp2 = client.post("/api/v1/strategy-catalog/momentum_effect/paper/deactivate")
    assert resp2.status_code == 200
    assert resp2.json()["activated"] is False


def test_activate_live_is_independent_of_paper(client):
    client.post("/api/v1/strategy-catalog/momentum_effect/live/activate")
    listed = client.get("/api/v1/strategy-catalog/momentum_effect/activation").json()
    assert listed["live"]["activated"] is True
    assert listed["paper"]["activated"] is False


def test_activate_rejects_unknown_key(client):
    resp = client.post("/api/v1/strategy-catalog/not_a_real_key/paper/activate")
    assert resp.status_code == 404


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


# ---------------------------------------------------------------------------
# Allocation (starting capital tracked against real trading)
# ---------------------------------------------------------------------------

def test_get_allocation_defaults_when_unset(client):
    resp = client.get("/api/v1/strategy-catalog/momentum_effect/allocation")
    assert resp.status_code == 200
    assert resp.json()["allocated_capital"] > 0


def test_put_allocation_round_trips(client):
    original = client.get("/api/v1/strategy-catalog/momentum_effect/allocation").json()["allocated_capital"]
    resp = client.put("/api/v1/strategy-catalog/momentum_effect/allocation", json={"allocated_capital": 2500})
    try:
        assert resp.status_code == 200
        assert resp.json()["allocated_capital"] == 2500
        assert client.get("/api/v1/strategy-catalog/momentum_effect/allocation").json()["allocated_capital"] == 2500
    finally:
        client.put("/api/v1/strategy-catalog/momentum_effect/allocation", json={"allocated_capital": original})


def test_put_allocation_rejects_non_positive(client):
    resp = client.put("/api/v1/strategy-catalog/momentum_effect/allocation", json={"allocated_capital": 0})
    assert resp.status_code == 400


def test_get_allocation_includes_per_stock_amount_defaulting_to_none(client):
    resp = client.get("/api/v1/strategy-catalog/momentum_effect/allocation")
    assert resp.status_code == 200
    assert resp.json()["per_stock_amount"] is None


def test_put_per_stock_amount_round_trips_independently_of_allocation(client):
    original_alloc = client.get("/api/v1/strategy-catalog/momentum_effect/allocation").json()["allocated_capital"]
    resp = client.put("/api/v1/strategy-catalog/momentum_effect/allocation", json={"per_stock_amount": 75})
    try:
        assert resp.status_code == 200
        body = resp.json()
        assert body["per_stock_amount"] == 75
        assert body["allocated_capital"] == original_alloc  # untouched by a per_stock_amount-only PUT

        cleared = client.put("/api/v1/strategy-catalog/momentum_effect/allocation", json={"per_stock_amount": None})
        assert cleared.json()["per_stock_amount"] is None
    finally:
        client.put("/api/v1/strategy-catalog/momentum_effect/allocation", json={"per_stock_amount": None})


def test_put_allocation_rejects_non_positive_per_stock_amount(client):
    resp = client.put("/api/v1/strategy-catalog/momentum_effect/allocation", json={"per_stock_amount": 0})
    assert resp.status_code == 400


def test_put_allocation_rejects_empty_body(client):
    resp = client.put("/api/v1/strategy-catalog/momentum_effect/allocation", json={})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Real tunable parameters (the Edit page)
# ---------------------------------------------------------------------------

def test_get_params_returns_schema_and_effective_values(client):
    resp = client.get("/api/v1/strategy-catalog/momentum_effect/params")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema"]["top_n"]["default"] == 10
    assert body["values"]["top_n"] == 10


def test_get_params_empty_schema_for_a_non_tunable_strategy(client):
    resp = client.get("/api/v1/strategy-catalog/mean_variance_djia/params")
    assert resp.status_code == 200
    assert resp.json()["schema"] == {}


def test_put_params_saves_and_is_reflected_in_get(client):
    resp = client.put("/api/v1/strategy-catalog/momentum_effect/params", json={"values": {"top_n": 4}})
    assert resp.status_code == 200
    assert resp.json()["values"]["top_n"] == 4
    try:
        assert client.get("/api/v1/strategy-catalog/momentum_effect/params").json()["values"]["top_n"] == 4
    finally:
        client.put("/api/v1/strategy-catalog/momentum_effect/params", json={"values": {"top_n": 10}})


def test_put_params_rejects_unknown_param_name(client):
    resp = client.put("/api/v1/strategy-catalog/momentum_effect/params", json={"values": {"not_a_param": 1}})
    assert resp.status_code == 400


def test_put_params_rejects_out_of_range(client):
    resp = client.put("/api/v1/strategy-catalog/momentum_effect/params", json={"values": {"top_n": 999}})
    assert resp.status_code == 400


def test_put_params_on_non_tunable_strategy_is_rejected(client):
    resp = client.put("/api/v1/strategy-catalog/mean_variance_djia/params", json={"values": {"anything": 1}})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Remove strategy
# ---------------------------------------------------------------------------

def test_delete_unknown_strategy_returns_404(client):
    resp = client.delete("/api/v1/strategy-catalog/not_a_real_key")
    assert resp.status_code == 404


def test_delete_and_restore_a_baseline_strategy(client):
    """Round-trips a real removal against leaderboard.json without leaving
    the committed config mutated -- restores the entry from the response's
    own state on the way out, same spirit as the allocation/params tests'
    try/finally reset."""
    config = catalog_module.load_leaderboard_config()
    original = [dict(s) for s in config["strategies"]]

    resp = client.delete("/api/v1/strategy-catalog/even_split_dow")
    try:
        assert resp.status_code == 200
        assert resp.json() == {"removed": "even_split_dow"}
        after = catalog_module.load_leaderboard_config()
        assert all(s.get("id") != "even_split_dow" for s in after["strategies"])
        # A second delete of the same now-gone key is a 404, not a repeat 200.
        assert client.delete("/api/v1/strategy-catalog/even_split_dow").status_code == 404
    finally:
        config["strategies"] = original
        catalog_module._save_leaderboard_config(config)


def test_delete_evicts_the_served_cache_too(client, tmp_path, monkeypatch):
    """Removing must also drop the entry from the cached catalog payload the
    page actually renders. Without this, a 200 removal stayed visible for up
    to CACHE_TTL_HOURS and the Remove button looked broken (found live
    2026-08-29: leaderboard.json updated, stale cache kept serving the
    entry). Pinned against the cache file, not the roster."""
    cache_file = tmp_path / "catalog_cache.json"
    monkeypatch.setattr(catalog_module, "CACHE_PATH", cache_file)
    catalog_module._save_cache({
        "computed_at": "2099-01-01T00:00:00+00:00",
        "entries": [
            {"key": "even_split_dow", "metrics": {}},
            {"key": "blue_chip_steady", "metrics": {}},
        ],
    })

    config = catalog_module.load_leaderboard_config()
    original = [dict(s) for s in config["strategies"]]
    resp = client.delete("/api/v1/strategy-catalog/even_split_dow")
    try:
        assert resp.status_code == 200
        cached = catalog_module._load_cache()
        keys = [e["key"] for e in cached["entries"]]
        assert "even_split_dow" not in keys
        assert "blue_chip_steady" in keys  # only the removed entry is evicted
    finally:
        config["strategies"] = original
        catalog_module._save_leaderboard_config(config)


def test_delete_rejects_ai_model_entries(client):
    config = catalog_module.load_leaderboard_config()
    model_id = next(s["id"] for s in config["strategies"] if s.get("label") == "Model")
    resp = client.delete(f"/api/v1/strategy-catalog/{model_id}")
    assert resp.status_code == 400
    # Still present -- the route must reject before mutating leaderboard.json.
    after = catalog_module.load_leaderboard_config()
    assert any(s.get("id") == model_id for s in after["strategies"])


# ---------------------------------------------------------------------------
# convert_agent_to_strategy (domain-level; the HTTP route is covered by
# test_agent_convert_to_strategy.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def _isolated_leaderboard_config(tmp_path, monkeypatch):
    config_path = tmp_path / "leaderboard.json"
    config_path.write_text(json.dumps({"strategies": []}), encoding="utf-8")
    monkeypatch.setattr(catalog_module, "LEADERBOARD_CONFIG_PATH", config_path)
    return config_path


def test_convert_agent_to_strategy_writes_an_llm_agent_entry(_isolated_leaderboard_config):
    entry = catalog_module.convert_agent_to_strategy(
        name="My Agent", model_id="anthropic/claude-haiku-4-5", strategy_prompt="Buy the dip.",
    )
    assert entry["strategy"] == "llm_agent"
    assert entry["model_id"] == "anthropic/claude-haiku-4-5"
    assert entry["label"] == "Baseline Strategy"
    assert entry["auto_compute"] is False
    assert "My Agent" in entry["description"]
    assert "Buy the dip." in entry["description"]

    config = catalog_module.load_leaderboard_config()
    assert any(s["id"] == entry["id"] for s in config["strategies"])


def test_convert_agent_to_strategy_rejects_blank_prompt(_isolated_leaderboard_config):
    with pytest.raises(ValueError, match="no trading instruction"):
        catalog_module.convert_agent_to_strategy(name="X", model_id=None, strategy_prompt="   ")


def test_convert_agent_to_strategy_rejects_over_length_prompt(_isolated_leaderboard_config):
    huge = "a" * (catalog_module.MAX_STRATEGY_PROMPT_CHARS + 1)
    with pytest.raises(ValueError, match="over the"):
        catalog_module.convert_agent_to_strategy(name="X", model_id=None, strategy_prompt=huge)


def test_convert_agent_to_strategy_dedupes_ids(_isolated_leaderboard_config):
    e1 = catalog_module.convert_agent_to_strategy(name="Same Name", model_id=None, strategy_prompt="A")
    e2 = catalog_module.convert_agent_to_strategy(name="Same Name", model_id=None, strategy_prompt="B")
    assert e1["id"] != e2["id"]


