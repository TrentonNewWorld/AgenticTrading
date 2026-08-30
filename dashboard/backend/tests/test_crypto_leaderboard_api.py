"""Crypto Competition + Live Trading Leaderboards. Mirrors
test_futures_leaderboard_api.py and test_forex_leaderboard_api.py exactly.
Fully stubbed backtester (no network).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.domain.crypto import catalog as catalog_module
from dashboard.backend.domain.crypto import leaderboard_service


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_module, "CACHE_PATH", tmp_path / "crypto_strategy_catalog_cache.json")
    monkeypatch.setattr(leaderboard_service, "LIVE_CACHE_PATH", tmp_path / "crypto_live_leaderboard_cache.json")


_CURVES_BY_KEY = {
    "cx_momentum_basket": [{"date": "2025-08-21", "equity": 1000.0}, {"date": "2026-08-21", "equity": 1200.0}],
    "cx_dip_reversion": [{"date": "2025-08-21", "equity": 1000.0}, {"date": "2026-08-21", "equity": 1050.0}],
}


@pytest.fixture(autouse=True)
def _stub_backtester(monkeypatch):
    import dashboard.backend.domain.crypto.backtester as backtester_module

    def _fake_run_backtest(code, symbols, start, end, initial_capital, **kwargs):
        return list(_CURVES_BY_KEY.get(_current_key[0], _CURVES_BY_KEY["cx_momentum_basket"]))

    monkeypatch.setattr(backtester_module, "run_backtest", _fake_run_backtest)


_current_key = [None]


@pytest.fixture(autouse=True)
def _track_current_strategy(monkeypatch):
    import dashboard.backend.domain.crypto.catalog as catalog_mod
    import dashboard.backend.domain.crypto.leaderboard_service as leaderboard_mod
    from dashboard.backend.domain.crypto.strategies import get_strategy as real_get_strategy

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
    # Ten entries since 2026-08-29. The fake-curve fixture only defines
    # distinct curves for the two starters; every new key falls back to the
    # momentum curve (1200), so exact ordering among those ties is
    # unspecified -- pin the parts that are: the full key set, dip_reversion
    # ranked strictly last (its 1050 curve is the unique loser), and a
    # contiguous 1..N ranking.
    assert set(keys_in_rank_order) == set(["cx_momentum_basket", "cx_dip_reversion", "cx_sma_cross", "cx_rsi_reversion", "cx_donchian_breakout", "cx_zscore_meanrev", "cx_ema_ribbon", "cx_vol_breakout", "cx_trend_dip", "cx_multiday_momentum"])
    assert keys_in_rank_order[-1] == "cx_dip_reversion"
    ranks = [e["rank"] for e in payload["entries"]]
    assert ranks == list(range(1, 11))


def test_contest_leaderboard_reuses_the_catalog_cache():
    catalog_payload = catalog_module.get_strategy_catalog()
    leaderboard_payload = leaderboard_service.get_leaderboard("contest")
    assert leaderboard_payload["computed_at"] == catalog_payload["computed_at"]


def test_live_leaderboard_is_a_preview_when_nothing_has_elapsed(monkeypatch):
    """leaderboard_crypto.json's live_season_start is 2026-08-23 -- if
    "today" IS that date, no day has elapsed yet."""
    import datetime as datetime_module

    import dashboard.backend.domain.crypto.leaderboard_service as mod

    class _FrozenDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime_module.datetime(2026, 8, 23, 12, 0, tzinfo=tz)

    monkeypatch.setattr(mod, "datetime", _FrozenDatetime)

    payload = mod.get_leaderboard("live")
    assert payload["status"] == "preview"
    assert payload["entries"] == []


def test_live_leaderboard_computes_once_days_have_elapsed(monkeypatch):
    import datetime as datetime_module

    import dashboard.backend.domain.crypto.leaderboard_service as mod

    class _FrozenDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime_module.datetime(2026, 9, 2, 12, 0, tzinfo=tz)

    monkeypatch.setattr(mod, "datetime", _FrozenDatetime)

    payload = mod.get_leaderboard("live")
    assert payload["status"] == "live"
    assert payload["entries"]
    assert payload["window"]["start_date"] < payload["window"]["end_date"]


def test_live_leaderboard_caches_between_calls(monkeypatch):
    import datetime as datetime_module

    import dashboard.backend.domain.crypto.leaderboard_service as mod

    class _FrozenDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime_module.datetime(2026, 9, 2, 12, 0, tzinfo=tz)

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
    resp = client.get("/api/v1/crypto/leaderboard")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["period"] == "contest"
    assert len(data["entries"]) == 10


def test_leaderboard_route_live(client):
    resp = client.get("/api/v1/crypto/leaderboard?period=live")
    assert resp.status_code == 200, resp.text
    assert resp.json()["period"] == "live"
