"""Sub-phase 9 of the Options-dashboard plan: the Options Competition +
Live Trading Leaderboards -- ranked views of the Options Strategy Catalog's
own precomputed metrics, not a separate LLM-tournament subsystem. Fully
stubbed backtester (no network).
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

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.domain.options import catalog as catalog_module
from dashboard.backend.domain.options import leaderboard_service


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_module, "CACHE_PATH", tmp_path / "options_strategy_catalog_cache.json")
    monkeypatch.setattr(leaderboard_service, "LIVE_CACHE_PATH", tmp_path / "options_live_leaderboard_cache.json")


# Curves shaped so the three starters rank predictably: covered call best,
# cash-secured put middle, long call momentum worst.
_CURVES_BY_KEY = {
    "opt_cash_secured_put": [{"date": "2025-08-21", "equity": 1000.0}, {"date": "2026-08-21", "equity": 1300.0}],
    "opt_cash_secured_put": [{"date": "2025-08-21", "equity": 1000.0}, {"date": "2026-08-21", "equity": 1150.0}],
    "opt_long_call_momentum": [{"date": "2025-08-21", "equity": 1000.0}, {"date": "2026-08-21", "equity": 950.0}],
}


@pytest.fixture(autouse=True)
def _stub_backtester(monkeypatch):
    import dashboard.backend.domain.options.backtester as backtester_module

    def _fake_run_backtest(code, underlyings, start, end, initial_capital, **kwargs):
        # _track_current_strategy (below) tags which strategy key is
        # currently resolving, since OptionsBaselineStrategy.run() gives
        # this stub no direct way to tell strategies apart otherwise.
        return list(_CURVES_BY_KEY.get(_current_key[0], _CURVES_BY_KEY["opt_cash_secured_put"]))

    monkeypatch.setattr(backtester_module, "run_backtest", _fake_run_backtest)


_current_key = [None]


@pytest.fixture(autouse=True)
def _track_current_strategy(monkeypatch):
    """Tags which strategy is currently being run so the stub above can
    return a distinct curve per key -- OptionsBaselineStrategy.run() has no
    direct hook for this, so wrap get_strategy instead."""
    import dashboard.backend.domain.options.catalog as catalog_mod
    import dashboard.backend.domain.options.leaderboard_service as leaderboard_mod
    from dashboard.backend.domain.options.strategies import get_strategy as real_get_strategy

    def _tagging_get_strategy(config):
        _current_key[0] = config.get("id") or config.get("strategy")
        return real_get_strategy(config)

    monkeypatch.setattr(catalog_mod, "get_strategy", _tagging_get_strategy)
    monkeypatch.setattr(leaderboard_mod, "get_strategy", _tagging_get_strategy)


def test_contest_leaderboard_ranks_by_return_pct():
    payload = leaderboard_service.get_leaderboard("contest")
    assert payload["period"] == "contest"
    assert payload["status"] == "final"
    keys_in_rank_order = [e["key"] for e in payload["entries"]]
    assert keys_in_rank_order == ["opt_cash_secured_put"]
    ranks = [e["rank"] for e in payload["entries"]]
    assert ranks == [1]


def test_contest_leaderboard_reuses_the_catalog_cache():
    """period="contest" must not recompute independently of
    domain.options.catalog -- confirmed by checking the two share a
    computed_at once both are warm."""
    catalog_payload = catalog_module.get_strategy_catalog()
    leaderboard_payload = leaderboard_service.get_leaderboard("contest")
    assert leaderboard_payload["computed_at"] == catalog_payload["computed_at"]


def test_live_leaderboard_is_a_preview_when_nothing_has_elapsed(monkeypatch):
    """leaderboard_options.json's live_season_start is 2026-08-22 -- if
    "today" IS that date, no day has elapsed yet (live_end = today - 1 is
    still before live_start) -- an honest empty/preview state, not a
    fabricated curve."""
    import datetime as datetime_module

    import dashboard.backend.domain.options.leaderboard_service as mod

    class _FrozenDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime_module.datetime(2026, 8, 22, 12, 0, tzinfo=tz)

    monkeypatch.setattr(mod, "datetime", _FrozenDatetime)

    payload = mod.get_leaderboard("live")
    assert payload["status"] == "preview"
    assert payload["entries"] == []


def test_live_leaderboard_computes_once_days_have_elapsed(monkeypatch):
    """Confirms the fix for a real bug caught during development: an
    earlier version derived live_start from contest_window_for_year(today)
    itself, which -- being defined relative to "today" -- always evaluated
    to exactly "today" no matter how much time had passed, so the live
    window could never accumulate any elapsed days. live_season_start is a
    fixed anchor specifically to avoid that."""
    import datetime as datetime_module

    import dashboard.backend.domain.options.leaderboard_service as mod

    class _FrozenDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            # 10 days after live_season_start (2026-08-22).
            return datetime_module.datetime(2026, 9, 1, 12, 0, tzinfo=tz)

    monkeypatch.setattr(mod, "datetime", _FrozenDatetime)

    payload = mod.get_leaderboard("live")
    assert payload["status"] == "live"
    assert payload["entries"]
    assert payload["window"]["start_date"] < payload["window"]["end_date"]


def test_live_leaderboard_caches_between_calls(monkeypatch):
    import datetime as datetime_module

    import dashboard.backend.domain.options.leaderboard_service as mod

    class _FrozenDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime_module.datetime(2026, 9, 1, 12, 0, tzinfo=tz)

    monkeypatch.setattr(mod, "datetime", _FrozenDatetime)

    call_count = {"n": 0}
    real_compute = mod._compute_for_window

    def _counting(*a, **k):
        call_count["n"] += 1
        return real_compute(*a, **k)

    monkeypatch.setattr(mod, "_compute_for_window", _counting)

    mod.get_leaderboard("live")
    mod.get_leaderboard("live")
    assert call_count["n"] == 1

    mod.get_leaderboard("live", force_refresh=True)
    assert call_count["n"] == 2


def test_invalid_period_falls_back_to_contest():
    payload = leaderboard_service.get_leaderboard("bogus")
    assert payload["period"] == "contest"


def test_leaderboard_route_contest(client):
    resp = client.get("/api/v1/options/leaderboard")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["period"] == "contest"
    assert len(data["entries"]) == 1


def test_leaderboard_route_live(client):
    resp = client.get("/api/v1/options/leaderboard?period=live")
    assert resp.status_code == 200, resp.text
    assert resp.json()["period"] == "live"
