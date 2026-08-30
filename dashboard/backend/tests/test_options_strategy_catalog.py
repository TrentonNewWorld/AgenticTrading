"""Sub-phase 8 of the Options-dashboard plan: the Options Strategy Catalog
(own roster, own cache, own registry -- not merged into the stocks
catalog). Backtester calls are stubbed (no network); the starter roster's
own decide_options() code is exercised for real through the sandbox, since
that part needs no network access either.
"""



from __future__ import annotations

# Blank-slate distribution guard: these tests pin the operator's configured
# strategy roster. A fresh clone/release ships every roster empty (strategies
# are distributed separately), and pinning absent content is meaningless
# there -- skip the whole module instead of failing a pristine checkout.
import json as _json
import pathlib as _pathlib
import pytest as _pytest

_ROSTER = _pathlib.Path(__file__).resolve().parents[2] / "config" / "leaderboard_options.json"
if not _json.loads(_ROSTER.read_text(encoding="utf-8")).get("strategies"):
    _pytest.skip("blank-slate build: leaderboard_options.json has no strategies configured", allow_module_level=True)

import json

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.domain.options import catalog as catalog_module


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_module, "CACHE_PATH", tmp_path / "options_strategy_catalog_cache.json")


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Copy the real leaderboard_options.json into a temp file so
    add/remove tests never touch the committed config."""
    import shutil

    src = catalog_module.LEADERBOARD_OPTIONS_CONFIG_PATH
    dst = tmp_path / "leaderboard_options.json"
    shutil.copy(src, dst)
    monkeypatch.setattr(catalog_module, "LEADERBOARD_OPTIONS_CONFIG_PATH", dst)


_FAKE_CURVE = [
    {"date": "2025-08-21", "equity": 1000.0},
    {"date": "2025-09-21", "equity": 1050.0},
    {"date": "2025-10-21", "equity": 1100.0},
]


@pytest.fixture(autouse=True)
def _stub_backtester(monkeypatch):
    """Every OptionsBaselineStrategy.run() delegates to
    domain.options.backtester.run_backtest via a fresh import inside the
    method body (not a module-level name in base.py) -- stub it on its
    owning module so every fresh `from ... import run_backtest` picks up
    the stub, so no test in this file makes a real Alpaca call."""
    import dashboard.backend.domain.options.backtester as backtester_module

    monkeypatch.setattr(backtester_module, "run_backtest", lambda *a, **k: list(_FAKE_CURVE))


# ---------------------------------------------------------------------------
# Roster / registry
# ---------------------------------------------------------------------------

def test_roster_matches_config():
    keys = [e.key for e in catalog_module._catalog_roster()]
    assert keys == ["opt_cash_secured_put"]


def test_catalog_entries_have_unique_keys():
    keys = [e.key for e in catalog_module._catalog_roster()]
    assert len(keys) == len(set(keys))


def test_every_roster_entry_resolves_and_has_code():
    from dashboard.backend.domain.options.strategies import get_strategy

    for entry in catalog_module._catalog_roster():
        strat = get_strategy(catalog_module._config_for(entry))
        assert strat.required_underlyings()
        assert strat.code().strip()
        assert "decide_options" in strat.code()


def test_starter_strategies_are_not_in_the_stocks_registry():
    """Confirms the user's explicit decision -- strategies do not carry over
    between dashboards -- by checking the stocks registry has no
    opt_-prefixed keys at all."""
    from dashboard.backend.domain.leaderboard.strategies.registry import available_strategies as stocks_strategies

    stocks_keys = set(stocks_strategies().keys())
    assert not any(k.startswith("opt_") for k in stocks_keys)


# ---------------------------------------------------------------------------
# _compute_all / caching
# ---------------------------------------------------------------------------

def test_compute_all_produces_one_entry_per_roster_strategy():
    payload = catalog_module._compute_all()
    assert len(payload["entries"]) == 1
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


# ---------------------------------------------------------------------------
# generate_report / build_export
# ---------------------------------------------------------------------------

def test_generate_report_matches_stubbed_curve():
    report = catalog_module.generate_report("opt_cash_secured_put")
    assert report["key"] == "opt_cash_secured_put"
    assert report["overall"]["starting_wallet"] == 1000.0
    assert report["overall"]["final"] == pytest.approx(1100.0)
    assert report["overall"]["pnl"] == pytest.approx(100.0)
    assert len(report["months"]) >= 1


def test_generate_report_unknown_key_raises_value_error():
    with pytest.raises(ValueError):
        catalog_module.generate_report("not_a_real_key")


def test_build_export_returns_real_executable_code_for_every_starter():
    """Unlike the stocks catalog, every Options starter has real,
    re-runnable source -- confirmed for all three."""
    for key in ("opt_cash_secured_put",):
        package = catalog_module.build_export(key)
        assert package["format"] == catalog_module.EXPORT_FORMAT
        assert "decide_options" in package["code"]


def test_add_to_catalog_and_export_round_trips():
    code = "def decide_options(as_of, positions, chain, account):\n    return []\n"
    entry = catalog_module.add_to_catalog(name="My Options Strat", description="test", code=code)
    assert entry["strategy"] == "opt_sandboxed"

    package = catalog_module.build_export(entry["id"])
    assert package["code"] == code

    removed = catalog_module.remove_strategy(entry["id"])
    assert removed is True
    assert catalog_module.remove_strategy(entry["id"]) is False


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

def test_list_catalog_route(client):
    resp = client.get("/api/v1/options/strategy-catalog")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["entries"]) == 1


def test_report_route(client):
    resp = client.get("/api/v1/options/strategy-catalog/opt_cash_secured_put/report")
    assert resp.status_code == 200, resp.text
    assert resp.json()["overall"]["final"] == pytest.approx(1100.0)


def test_report_route_unknown_key_is_404(client):
    resp = client.get("/api/v1/options/strategy-catalog/nope/report")
    assert resp.status_code == 404


def test_export_route(client):
    resp = client.get("/api/v1/options/strategy-catalog/opt_cash_secured_put/export")
    assert resp.status_code == 200, resp.text
    assert "attachment" in resp.headers.get("content-disposition", "")
    package = json.loads(resp.content)
    assert "decide_options" in package["code"]


def test_delete_route(client):
    code = "def decide_options(as_of, positions, chain, account):\n    return []\n"
    entry = catalog_module.add_to_catalog(name="Deletable", description="", code=code)
    resp = client.delete(f"/api/v1/options/strategy-catalog/{entry['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"removed": entry["id"]}


def test_delete_route_unknown_key_is_404(client):
    resp = client.delete("/api/v1/options/strategy-catalog/nope")
    assert resp.status_code == 404
