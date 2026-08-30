"""End-to-end test of the Options dashboard's per-interval tick
(domain/options/engine.py), driven entirely by fakes -- no real Alpaca
calls, no real market clock. Mirrors test_manual10_engine.py's isolation
pattern (options/repository.py delegates to manual10/repository.py's own
DB_PATH, so isolating that one module isolates both).
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

import pytest

from dashboard.backend.domain.options import engine, repository as repo

TRADING_DATE = "2026-08-21"


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    db_path = tempfile.mktemp(suffix=".db")
    import dashboard.backend.domain.manual10.repository as manual10_repo_module
    monkeypatch.setattr(manual10_repo_module, "DB_PATH", db_path)
    manual10_repo_module._init_schema()
    manual10_repo_module._migrate_schema()
    yield


@pytest.fixture(autouse=True)
def _no_real_money(monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_OPTIONS_EXECUTE", raising=False)
    monkeypatch.delenv("ALPACA_LIVE_OPTIONS_EXECUTE", raising=False)


@pytest.fixture(autouse=True)
def _no_real_wallet_lookup(monkeypatch):
    """See test_futures_engine.py's identical fixture for why this must
    patch domain.wallets directly, not options/engine.py's namespace."""
    import dashboard.backend.domain.wallets as wallets_module
    monkeypatch.setattr(wallets_module, "get_broker_cash_basis", lambda *a, **k: None)


class _FakeSession:
    def __init__(self, now):
        self.now = now


_COVERED_CALL_STRATEGY = """
def decide_options(as_of, positions, chain, account):
    # A real vertical spread: both legs on the SAME underlying (AAPL) --
    # a real Alpaca MLEG order requires this, and it's what one "leg group"
    # is meant to represent (one strategic position on one underlying).
    if positions:
        return []
    aapl_calls = sorted(
        (c for c in chain.get("AAPL", []) if c["right"] == "C"),
        key=lambda c: c["strike"],
    )
    if len(aapl_calls) < 2:
        return []
    low, high = aapl_calls[0], aapl_calls[1]
    return [
        {"action": "open", "symbol": low["symbol"], "side": "buy", "qty": 1, "leg_role": "option"},
        {"action": "open", "symbol": high["symbol"], "side": "sell", "qty": 1, "leg_role": "option"},
    ]
"""

_FAKE_CHAIN = {
    "AAPL": [
        {"symbol": "AAPL260918C00180000", "underlying": "AAPL", "expiration": "2026-09-18",
         "right": "C", "strike": 180.0, "bid": 5.0, "ask": 5.2, "last": 5.1},
        {"symbol": "AAPL260918C00190000", "underlying": "AAPL", "expiration": "2026-09-18",
         "right": "C", "strike": 190.0, "bid": 2.0, "ask": 2.2, "last": 2.1},
    ],
}


def _create_approved_strategy(code=_COVERED_CALL_STRATEGY, name="Covered Call Test", interval=5):
    strategy = repo.create_uploaded_strategy(
        name=name, description="test", code=code, interval_minutes=interval,
        review_status="approved", review_notes="test",
    )
    return strategy


def test_tick_skips_unapproved_strategy(monkeypatch):
    strategy = repo.create_uploaded_strategy(
        name="Pending Strat", description="", code=_COVERED_CALL_STRATEGY, interval_minutes=5,
        review_status="pending", review_notes="",
    )
    session = _FakeSession(datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)
    assert result == {"phase": "not_approved"}


def test_tick_opens_a_two_leg_position_under_one_leg_group(monkeypatch):
    strategy = _create_approved_strategy()
    monkeypatch.setattr(engine, "_build_chain", lambda underlyings: _FAKE_CHAIN)

    session = _FakeSession(datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)

    assert result["phase"] == "holding"
    assert result["orders"] == 2

    positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert len(positions) == 2
    leg_group_ids = {p["leg_group_id"] for p in positions}
    assert len(leg_group_ids) == 1  # both legs share one group
    symbols = {p["symbol"] for p in positions}
    assert symbols == {"AAPL260918C00180000", "AAPL260918C00190000"}
    roles = {p["leg_role"] for p in positions}
    assert roles == {"option"}
    underlyings = {p["underlying_symbol"] for p in positions}
    assert underlyings == {"AAPL"}
    # Entry prices came from the fake chain's mid (bid+ask)/2.
    low_leg = next(p for p in positions if p["symbol"] == "AAPL260918C00180000")
    assert low_leg["entry_price"] == pytest.approx(5.1)
    high_leg = next(p for p in positions if p["symbol"] == "AAPL260918C00190000")
    assert high_leg["entry_price"] == pytest.approx(2.1)


def test_tick_respects_interval_and_waits(monkeypatch):
    strategy = _create_approved_strategy(interval=60)
    monkeypatch.setattr(engine, "_build_chain", lambda underlyings: _FAKE_CHAIN)

    first_now = datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)
    session1 = _FakeSession(first_now)
    engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session1)

    # 10 minutes later, well inside the 60-minute interval -- must wait.
    session2 = _FakeSession(datetime(2026, 8, 21, 15, 10, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session2)
    assert result == {"phase": "waiting"}


def test_tick_with_no_chain_data_returns_error_phase(monkeypatch):
    strategy = _create_approved_strategy()
    monkeypatch.setattr(engine, "_build_chain", lambda underlyings: {})

    session = _FakeSession(datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)
    assert result == {"phase": "error"}


def test_tick_never_calls_real_broker_when_execute_flag_unset(monkeypatch):
    """The default (ALPACA_PAPER_OPTIONS_EXECUTE unset) must never construct
    a real broker client -- positions are recorded as simulated fills only."""
    strategy = _create_approved_strategy()
    monkeypatch.setattr(engine, "_build_chain", lambda underlyings: _FAKE_CHAIN)

    def _boom(*a, **k):
        raise AssertionError("must not construct a real broker client when execute is off")

    monkeypatch.setattr(
        "dashboard.backend.infrastructure.brokers.alpaca_paper_options.AlpacaPaperOptionsClient",
        _boom,
    )

    session = _FakeSession(datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)
    assert result["phase"] == "holding"


def test_tick_closes_a_position_on_close_intent(monkeypatch):
    close_strategy_code = """
def decide_options(as_of, positions, chain, account):
    if not positions:
        return [
            {"action": "open", "symbol": "AAPL260918C00180000", "side": "buy", "qty": 1, "leg_role": "single"},
        ]
    return [
        {"action": "close", "symbol": positions[0]["symbol"], "side": "sell", "qty": 1, "leg_role": "single"},
    ]
"""
    strategy = _create_approved_strategy(code=close_strategy_code, interval=5)
    monkeypatch.setattr(engine, "_build_chain", lambda underlyings: _FAKE_CHAIN)

    session1 = _FakeSession(datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc))
    engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session1)
    open_positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert len(open_positions) == 1

    session2 = _FakeSession(datetime(2026, 8, 21, 15, 10, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session2)
    assert result["closes"] == 1

    open_after = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert len(open_after) == 0
    closed = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="closed")
    assert len(closed) == 1
    assert closed[0]["close_reason"] == "strategy_close"
