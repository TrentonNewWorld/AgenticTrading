"""End-to-end test of the Futures dashboard's per-interval tick
(domain/futures/engine.py), driven entirely by fakes -- no real Yahoo
Finance calls. Mirrors test_options_engine.py's isolation pattern.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

import pytest

from dashboard.backend.domain.futures import engine, repository as repo

TRADING_DATE = "2026-08-23"


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
    monkeypatch.delenv("TRADOVATE_PAPER_EXECUTE", raising=False)


@pytest.fixture(autouse=True)
def _no_real_wallet_lookup(monkeypatch):
    """get_broker_cash_basis would otherwise make a real Tradovate/Alpaca
    network call on every tick (conftest.py does not strip ALPACA_API_KEY/
    SECRET_KEY the way it does the *_EXECUTE flags) -- every test below
    assumes the $1,000 simulated-wallet fallback, matching this module's
    behavior before domain/wallets.py existed. engine.py does the import
    fresh inside the function body each call, so patching the source
    module's attribute (not engine.py's namespace) is what actually takes
    effect."""
    import dashboard.backend.domain.wallets as wallets_module
    monkeypatch.setattr(wallets_module, "get_broker_cash_basis", lambda *a, **k: None)


class _FakeSession:
    def __init__(self, now):
        self.now = now


# A cheap micro-contract price, not ES=F's real ~$5,000+ notional -- the
# whole point of the cash-sufficiency regression test below is that a price
# exceeding the $1,000 wallet gets refused, so every OTHER test here needs a
# price that actually fits, or it would hit that same refusal by accident.
_FAKE_QUOTES = {"MES=F": {"symbol": "MES=F", "price": 600.0, "prev_close": 590.0}}


def _open_strategy_code():
    return """
def decide_futures(as_of, positions, quotes, account):
    if positions:
        return []
    return [{"action": "open", "symbol": "MES=F", "side": "buy", "qty": 1}]
"""


def _create_approved_strategy(code=None, name="Test Futures Strat", interval=5):
    return repo.create_uploaded_strategy(
        name=name, description="test", code=code or _open_strategy_code(), interval_minutes=interval,
        review_status="approved", review_notes="test",
    )


def test_tick_skips_unapproved_strategy():
    strategy = repo.create_uploaded_strategy(
        name="Pending Strat", description="", code=_open_strategy_code(), interval_minutes=5,
        review_status="pending", review_notes="",
    )
    session = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)
    assert result == {"phase": "not_approved"}


def test_tick_opens_a_position(monkeypatch):
    strategy = _create_approved_strategy()
    monkeypatch.setattr(engine, "get_futures_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))

    session = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)

    assert result["phase"] == "holding"
    assert result["orders"] == 1
    positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert len(positions) == 1
    assert positions[0]["symbol"] == "MES=F"
    assert positions[0]["entry_price"] == pytest.approx(600.0)


def test_tick_respects_interval_and_waits(monkeypatch):
    strategy = _create_approved_strategy(interval=60)
    monkeypatch.setattr(engine, "get_futures_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))

    session1 = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session1)

    session2 = _FakeSession(datetime(2026, 8, 23, 15, 10, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session2)
    assert result == {"phase": "waiting"}


def test_tick_with_no_quotes_returns_error_phase(monkeypatch):
    strategy = _create_approved_strategy()
    monkeypatch.setattr(engine, "get_futures_quotes_batch", lambda symbols: {})

    session = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)
    assert result == {"phase": "error"}


def test_tick_never_calls_real_broker_when_execute_flag_unset(monkeypatch):
    strategy = _create_approved_strategy()
    monkeypatch.setattr(engine, "get_futures_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))

    def _boom(*a, **k):
        raise AssertionError("must not construct a real broker client when execute is off")

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.tradovate_paper.TradovatePaperClient", _boom)

    session = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)
    assert result["phase"] == "holding"


def test_tick_closes_a_position_on_close_intent(monkeypatch):
    close_strategy_code = """
def decide_futures(as_of, positions, quotes, account):
    if not positions:
        return [{"action": "open", "symbol": "MES=F", "side": "buy", "qty": 1}]
    return [{"action": "close", "symbol": positions[0]["symbol"], "side": "sell", "qty": 1}]
"""
    strategy = _create_approved_strategy(code=close_strategy_code, interval=5)
    monkeypatch.setattr(engine, "get_futures_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))

    session1 = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session1)
    open_positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert len(open_positions) == 1

    session2 = _FakeSession(datetime(2026, 8, 23, 15, 10, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session2)
    assert result["closes"] == 1
    closed = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="closed")
    assert len(closed) == 1
    assert closed[0]["close_reason"] == "strategy_close"


def test_tick_refuses_an_open_that_would_exceed_the_wallet(monkeypatch):
    """Regression test for a real bug caught by live-verifying against real
    Yahoo data: the live tick always passed a hardcoded {"cash": 1000.0}
    regardless of what was already open, and nothing checked cost against
    it before opening -- a single ES=F contract at ~$5,000-7,700 notional
    against a $1,000 wallet is unlimited implicit leverage, and combined
    with the same gap in the backtester produced a 529% "annual return"
    that was really just this bug."""
    expensive_quotes = {"MES=F": {"symbol": "MES=F", "price": 5000.0, "prev_close": 4950.0}}
    strategy = _create_approved_strategy()
    monkeypatch.setattr(engine, "get_futures_quotes_batch", lambda symbols: dict(expensive_quotes))

    session = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)

    assert result["orders"] == 0
    positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert positions == []


def test_tick_accounts_for_capital_already_committed_to_open_positions(monkeypatch):
    """Two-tick scenario: the first tick opens a position that consumes
    most of the $1,000 wallet; the second tick's strategy tries to open a
    second position of the same size and must be refused, proving cash is
    computed from currently-open positions each tick, not a flat constant."""
    code = """
def decide_futures(as_of, positions, quotes, account):
    return [{"action": "open", "symbol": "MES=F", "side": "buy", "qty": 1}]
"""
    strategy = _create_approved_strategy(code=code, interval=5)
    cheap_quotes = {"MES=F": {"symbol": "MES=F", "price": 600.0, "prev_close": 590.0}}
    monkeypatch.setattr(engine, "get_futures_quotes_batch", lambda symbols: dict(cheap_quotes))

    session1 = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result1 = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session1)
    assert result1["orders"] == 1  # $600 fits in $1,000

    session2 = _FakeSession(datetime(2026, 8, 23, 15, 10, tzinfo=timezone.utc))
    result2 = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session2)
    assert result2["orders"] == 0  # a second $600 position would need $1,200 total, only $400 remains

    positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert len(positions) == 1


def test_tick_aggregator_advances_every_activated_strategy(monkeypatch):
    """Regression: engine.tick_uploaded_strategy() existed but nothing ever
    called it for a whole strategy list -- activating a Futures strategy did
    nothing at all, since there was no aggregator loop (unlike Manual10,
    which has had engine.tick() from the start) and no scheduler to drive
    one. tick() is that missing aggregator; this pins that activating a
    strategy and calling tick() actually opens a position."""
    strategy = _create_approved_strategy()
    repo.set_selected(TRADING_DATE, strategy["key"], True)
    repo.set_activated(TRADING_DATE, strategy["key"], True)
    monkeypatch.setattr(engine, "get_futures_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))
    monkeypatch.setattr(
        engine.market_clock, "get_today_session",
        lambda: engine.market_clock.TodaySession(
            trading_date=engine.market_clock.date(2026, 8, 23), has_session=True,
            open_at=None, close_at=None, now=datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc),
        ),
    )

    result = engine.tick()

    assert result["strategies"][strategy["key"]]["phase"] == "holding"
    positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert len(positions) == 1


def test_tick_aggregator_skips_unactivated_strategies():
    strategy = _create_approved_strategy()
    repo.set_selected(TRADING_DATE, strategy["key"], True)
    # Deliberately not activated.
    result = engine.tick()
    assert strategy["key"] not in result["strategies"]


def test_tradovate_credentials_prefers_a_saved_connection(monkeypatch):
    from dashboard.backend.domain.connections.repository import connection_store

    monkeypatch.setattr(
        connection_store, "get_credentials_any_user",
        lambda provider: {
            "username": "trader1", "password": "pw", "cid": "1234", "sec": "s3cr3t",
            "account_spec": "trader1", "account_id": "555",
        } if provider == "tradovate" else None,
    )
    creds = engine._tradovate_credentials()
    assert creds is not None
    assert creds.name == "trader1"
    assert creds.account_id == 555


def test_tradovate_credentials_is_none_when_nothing_saved(monkeypatch):
    from dashboard.backend.domain.connections.repository import connection_store

    monkeypatch.setattr(connection_store, "get_credentials_any_user", lambda provider: None)
    assert engine._tradovate_credentials() is None


def test_tick_sizes_against_a_connected_tradovate_balance_instead_of_1000(monkeypatch):
    """Regression: a connected Tradovate account's real balance should size
    new positions, not the flat $1,000 simulated wallet -- previously the
    engine never checked, so a real 5-figure demo account still capped
    every strategy at $1,000."""
    import dashboard.backend.domain.wallets as wallets_module
    monkeypatch.setattr(wallets_module, "get_broker_cash_basis", lambda asset_class, *a, **k: 50_000.0 if asset_class == "futures" else None)

    strategy = _create_approved_strategy()
    expensive_quotes = {"MES=F": {"symbol": "MES=F", "price": 5000.0, "prev_close": 4950.0}}
    monkeypatch.setattr(engine, "get_futures_quotes_batch", lambda symbols: dict(expensive_quotes))

    session = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)

    # Would have been refused against the old $1,000 constant (see the
    # sibling wallet-exceeded test above); a $50k connected balance fits it.
    assert result["orders"] == 1
    positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert len(positions) == 1
