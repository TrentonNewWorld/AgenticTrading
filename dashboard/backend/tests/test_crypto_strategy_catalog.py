"""Crypto Strategy Catalog (own roster, own cache, own registry). Mirrors
test_futures_strategy_catalog.py and test_forex_strategy_catalog.py exactly.
Backtester calls are stubbed (no network); the starter roster's own
decide_crypto() code is exercised for real through the sandbox, since that
part needs no network access either.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.domain.crypto import catalog as catalog_module


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_module, "CACHE_PATH", tmp_path / "crypto_strategy_catalog_cache.json")


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import shutil

    src = catalog_module.LEADERBOARD_CRYPTO_CONFIG_PATH
    dst = tmp_path / "leaderboard_crypto.json"
    shutil.copy(src, dst)
    monkeypatch.setattr(catalog_module, "LEADERBOARD_CRYPTO_CONFIG_PATH", dst)


_FAKE_CURVE = [
    {"date": "2025-08-21", "equity": 1000.0},
    {"date": "2025-09-21", "equity": 1050.0},
    {"date": "2025-10-21", "equity": 1100.0},
]


@pytest.fixture(autouse=True)
def _stub_backtester(monkeypatch):
    import dashboard.backend.domain.crypto.backtester as backtester_module

    monkeypatch.setattr(backtester_module, "run_backtest", lambda *a, **k: list(_FAKE_CURVE))


def test_starter_roster_has_two_entries():
    # 2026-08-29: the roster grew from the two starters to ten -- eight
    # indicator classics were added when quotes gained trailing-close history.
    keys = [e.key for e in catalog_module._catalog_roster()]
    assert keys == ["cx_momentum_basket", "cx_dip_reversion", "cx_sma_cross", "cx_rsi_reversion", "cx_donchian_breakout", "cx_zscore_meanrev", "cx_ema_ribbon", "cx_vol_breakout", "cx_trend_dip", "cx_multiday_momentum"]


def test_catalog_entries_have_unique_keys():
    keys = [e.key for e in catalog_module._catalog_roster()]
    assert len(keys) == len(set(keys))


def test_every_roster_entry_resolves_and_has_code():
    from dashboard.backend.domain.crypto.strategies import get_strategy

    for entry in catalog_module._catalog_roster():
        strat = get_strategy(catalog_module._config_for(entry))
        assert strat.required_symbols()
        assert strat.code().strip()
        assert "decide_crypto" in strat.code()


def test_starter_strategies_are_not_in_other_registries():
    from dashboard.backend.domain.forex.strategies import available_strategies as forex_strategies
    from dashboard.backend.domain.futures.strategies import available_strategies as futures_strategies
    from dashboard.backend.domain.leaderboard.strategies.registry import available_strategies as stocks_strategies
    from dashboard.backend.domain.options.strategies import available_strategies as options_strategies

    for registry in (stocks_strategies(), options_strategies(), futures_strategies(), forex_strategies()):
        assert not any(k.startswith("cx_") for k in registry.keys())


def test_compute_all_produces_one_entry_per_roster_strategy():
    payload = catalog_module._compute_all()
    assert len(payload["entries"]) == 10
    for entry in payload["entries"]:
        assert entry["metrics"]["final"] == pytest.approx(1100.0)
        assert entry["equity_curve"]


def test_metrics_on_empty_curve():
    metrics = catalog_module._metrics([])
    assert metrics["final"] == catalog_module.INITIAL_CAPITAL
    assert metrics["return_pct"] == 0.0


def test_get_strategy_catalog_caches_between_calls(monkeypatch):
    call_count = {"n": 0}
    real_compute = catalog_module._compute_all

    def _counting_compute(*a, **k):
        call_count["n"] += 1
        return real_compute(*a, **k)

    monkeypatch.setattr(catalog_module, "_compute_all", _counting_compute)

    first = catalog_module.get_strategy_catalog()
    second = catalog_module.get_strategy_catalog()
    assert call_count["n"] == 1
    assert first["computed_at"] == second["computed_at"]

    catalog_module.get_strategy_catalog(force_refresh=True)
    assert call_count["n"] == 2


def test_generate_report_matches_stubbed_curve():
    report = catalog_module.generate_report("cx_momentum_basket")
    assert report["key"] == "cx_momentum_basket"
    assert report["overall"]["starting_wallet"] == 1000.0
    assert report["overall"]["final"] == pytest.approx(1100.0)
    assert report["overall"]["pnl"] == pytest.approx(100.0)
    assert len(report["months"]) >= 1


def test_generate_report_unknown_key_raises_value_error():
    with pytest.raises(ValueError):
        catalog_module.generate_report("not_a_real_key")


def test_build_export_returns_real_executable_code_for_every_starter():
    for key in ("cx_momentum_basket", "cx_dip_reversion"):
        package = catalog_module.build_export(key)
        assert package["format"] == catalog_module.EXPORT_FORMAT
        assert "decide_crypto" in package["code"]


def test_add_to_catalog_and_export_round_trips():
    code = "def decide_crypto(as_of, positions, quotes, account):\n    return []\n"
    entry = catalog_module.add_to_catalog(name="My Crypto Strat", description="test", code=code)
    assert entry["strategy"] == "cx_sandboxed"

    package = catalog_module.build_export(entry["id"])
    assert package["code"] == code

    removed = catalog_module.remove_strategy(entry["id"])
    assert removed is True
    assert catalog_module.remove_strategy(entry["id"]) is False


def test_list_catalog_route(client):
    resp = client.get("/api/v1/crypto/strategy-catalog")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["entries"]) == 10


def test_report_route(client):
    resp = client.get("/api/v1/crypto/strategy-catalog/cx_dip_reversion/report")
    assert resp.status_code == 200, resp.text
    assert resp.json()["overall"]["final"] == pytest.approx(1100.0)


def test_report_route_unknown_key_is_404(client):
    resp = client.get("/api/v1/crypto/strategy-catalog/nope/report")
    assert resp.status_code == 404


def test_export_route(client):
    resp = client.get("/api/v1/crypto/strategy-catalog/cx_momentum_basket/export")
    assert resp.status_code == 200, resp.text
    assert "attachment" in resp.headers.get("content-disposition", "")
    package = json.loads(resp.content)
    assert "decide_crypto" in package["code"]


def test_delete_route(client):
    code = "def decide_crypto(as_of, positions, quotes, account):\n    return []\n"
    entry = catalog_module.add_to_catalog(name="Deletable", description="", code=code)
    resp = client.delete(f"/api/v1/crypto/strategy-catalog/{entry['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"removed": entry["id"]}


def test_delete_route_unknown_key_is_404(client):
    resp = client.delete("/api/v1/crypto/strategy-catalog/nope")
    assert resp.status_code == 404
