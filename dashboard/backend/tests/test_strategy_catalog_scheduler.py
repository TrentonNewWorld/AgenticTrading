"""domain/leaderboard/catalog_scheduler.py -- the daily "run every activated
Strategy Catalog entry once" loop. Driven entirely by fakes: no real Alpaca
calendar or trading calls.
"""

from __future__ import annotations

from datetime import date

import pytest

from dashboard.backend.domain.leaderboard import catalog_activation, catalog_scheduler


@pytest.fixture(autouse=True)
def _clean_activations():
    """catalog_activation shares the whole test-session DB (conftest.py's
    single temp DATABASE_PATH) -- deactivate anything this file touches so
    it never leaks into another test file's assertions."""
    yield
    for mode in ("paper", "live"):
        for key in ("momentum_effect", "buy_hold"):
            catalog_activation.deactivate(key, mode)


def test_is_probably_a_trading_day_rejects_weekends():
    # 2026-08-22 is a Saturday, 2026-08-23 a Sunday, 2026-08-24 a Monday.
    assert catalog_scheduler._is_probably_a_trading_day(date(2026, 8, 22)) is False
    assert catalog_scheduler._is_probably_a_trading_day(date(2026, 8, 23)) is False
    assert catalog_scheduler._is_probably_a_trading_day(date(2026, 8, 24)) is True


def test_tick_skips_entirely_on_a_weekend(monkeypatch):
    """Regression: tick() used to gate on manual10/market_clock.py's
    get_today_session(), which needs live Alpaca *paper* calendar access --
    an invalid/expired paper key silently blocked every activated
    strategy's tick, including live-mode ones with nothing to do with paper
    trading (caught live: a user's activated live strategy never ran once,
    ever, for exactly this reason). today_trading_date() is network-free,
    so this must keep working even when Alpaca is entirely unreachable."""
    calls = []
    monkeypatch.setattr(catalog_scheduler, "today_trading_date", lambda: date(2026, 8, 22))  # Saturday
    monkeypatch.setattr(catalog_activation, "list_activated", lambda mode: calls.append(mode) or [])
    catalog_scheduler.tick()
    assert calls == []


def test_tick_never_calls_the_live_alpaca_calendar(monkeypatch):
    """The exact regression this file exists to pin: tick() must not import
    or call anything that reaches Alpaca's network API just to decide
    whether to run."""
    def _boom():
        raise AssertionError("tick() must not call the live Alpaca calendar")

    monkeypatch.setattr(catalog_scheduler, "today_trading_date", lambda: date(2026, 8, 24))
    monkeypatch.setattr(catalog_activation, "list_activated", lambda mode: [])
    from dashboard.backend.domain.manual10 import market_clock
    monkeypatch.setattr(market_clock, "get_today_session", _boom)

    catalog_scheduler.tick()  # must not raise


def test_tick_runs_every_activated_strategy_and_records_the_result(monkeypatch):
    catalog_activation.activate("momentum_effect", "paper", user_id=None)
    monkeypatch.setattr(catalog_scheduler, "today_trading_date", lambda: date(2026, 8, 24))

    calls = []

    async def fake_run_paper(*, strategy_key, symbols, dry_run, user_id):
        calls.append((strategy_key, dry_run, user_id))
        return {"status": "completed"}

    import dashboard.backend.execution.alpaca_paper_service as paper_service
    monkeypatch.setattr(paper_service, "run_paper_for_strategy", fake_run_paper)

    catalog_scheduler.tick()

    assert calls == [("momentum_effect", False, None)]
    row = catalog_activation.get("momentum_effect", "paper")
    assert row["last_run_trading_date"] == "2026-08-24"
    assert row["last_run_status"] == "completed"


def test_tick_does_not_rerun_the_same_trading_date(monkeypatch):
    catalog_activation.activate("momentum_effect", "paper", user_id=None)
    catalog_activation.record_tick("momentum_effect", "paper", "2026-08-24", "completed")
    monkeypatch.setattr(catalog_scheduler, "today_trading_date", lambda: date(2026, 8, 24))

    calls = []

    async def fake_run_paper(**kwargs):
        calls.append(kwargs)
        return {"status": "completed"}

    import dashboard.backend.execution.alpaca_paper_service as paper_service
    monkeypatch.setattr(paper_service, "run_paper_for_strategy", fake_run_paper)

    catalog_scheduler.tick()

    assert calls == []  # already ran today -- must not run twice


def test_tick_runs_again_on_a_new_trading_date(monkeypatch):
    catalog_activation.activate("momentum_effect", "paper", user_id=None)
    catalog_activation.record_tick("momentum_effect", "paper", "2026-08-21", "completed")
    monkeypatch.setattr(catalog_scheduler, "today_trading_date", lambda: date(2026, 8, 24))

    calls = []

    async def fake_run_paper(**kwargs):
        calls.append(kwargs)
        return {"status": "completed"}

    import dashboard.backend.execution.alpaca_paper_service as paper_service
    monkeypatch.setattr(paper_service, "run_paper_for_strategy", fake_run_paper)

    catalog_scheduler.tick()

    assert len(calls) == 1
    assert catalog_activation.get("momentum_effect", "paper")["last_run_trading_date"] == "2026-08-24"


def test_tick_records_the_error_but_does_not_raise(monkeypatch):
    catalog_activation.activate("momentum_effect", "live", user_id=None)
    monkeypatch.setattr(catalog_scheduler, "today_trading_date", lambda: date(2026, 8, 24))

    async def fake_run_live(**kwargs):
        raise ValueError("alpaca_account_unavailable")

    import dashboard.backend.execution.alpaca_live_service as live_service
    monkeypatch.setattr(live_service, "run_live_for_strategy", fake_run_live)

    catalog_scheduler.tick()  # must not raise

    row = catalog_activation.get("momentum_effect", "live")
    assert row["last_run_trading_date"] == "2026-08-24"
    assert "alpaca_account_unavailable" in row["last_run_status"]


def test_paper_and_live_activations_are_independent(monkeypatch):
    catalog_activation.activate("momentum_effect", "paper", user_id=None)
    monkeypatch.setattr(catalog_scheduler, "today_trading_date", lambda: date(2026, 8, 24))

    live_calls = []

    async def fake_run_live(**kwargs):
        live_calls.append(kwargs)
        return {"status": "completed"}

    async def fake_run_paper(**kwargs):
        return {"status": "completed"}

    import dashboard.backend.execution.alpaca_live_service as live_service
    import dashboard.backend.execution.alpaca_paper_service as paper_service
    monkeypatch.setattr(live_service, "run_live_for_strategy", fake_run_live)
    monkeypatch.setattr(paper_service, "run_paper_for_strategy", fake_run_paper)

    catalog_scheduler.tick()

    assert live_calls == []  # only paper was activated
