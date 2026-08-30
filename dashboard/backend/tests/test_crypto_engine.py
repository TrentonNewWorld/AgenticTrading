"""End-to-end test of the Crypto dashboard's per-interval tick
(domain/crypto/engine.py), driven entirely by fakes -- no real Alpaca
calls. Mirrors test_futures_engine.py and test_forex_engine.py exactly.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

import pytest

from dashboard.backend.domain.crypto import engine, repository as repo

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
    monkeypatch.delenv("ALPACA_PAPER_CRYPTO_EXECUTE", raising=False)


@pytest.fixture(autouse=True)
def _no_real_wallet_lookup(monkeypatch):
    """See test_futures_engine.py's identical fixture for why this must
    patch domain.wallets directly, not crypto/engine.py's namespace."""
    import dashboard.backend.domain.wallets as wallets_module
    monkeypatch.setattr(wallets_module, "get_broker_cash_basis", lambda *a, **k: None)


class _FakeSession:
    def __init__(self, now):
        self.now = now


_FAKE_QUOTES = {"BTC/USD": {"symbol": "BTC/USD", "price": 77000.0, "prev_close": 76500.0}}


def _open_strategy_code():
    return """
def decide_crypto(as_of, positions, quotes, account):
    if positions:
        return []
    return [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.005}]
"""


def _create_approved_strategy(code=None, name="Test Crypto Strat", interval=5):
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


def test_tick_opens_a_fractional_position(monkeypatch):
    strategy = _create_approved_strategy()
    monkeypatch.setattr(engine, "get_crypto_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))

    session = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)

    assert result["phase"] == "holding"
    assert result["orders"] == 1
    positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTC/USD"
    assert positions[0]["shares"] == pytest.approx(0.005)
    assert positions[0]["entry_price"] == pytest.approx(77000.0)


def test_tick_respects_interval_and_waits(monkeypatch):
    strategy = _create_approved_strategy(interval=60)
    monkeypatch.setattr(engine, "get_crypto_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))

    session1 = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session1)

    session2 = _FakeSession(datetime(2026, 8, 23, 15, 10, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session2)
    assert result == {"phase": "waiting"}


def test_tick_with_no_quotes_returns_error_phase(monkeypatch):
    strategy = _create_approved_strategy()
    monkeypatch.setattr(engine, "get_crypto_quotes_batch", lambda symbols: {})

    session = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)
    assert result == {"phase": "error"}


def test_tick_never_calls_real_broker_when_execute_flag_unset(monkeypatch):
    strategy = _create_approved_strategy()
    monkeypatch.setattr(engine, "get_crypto_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))

    def _boom(*a, **k):
        raise AssertionError("must not construct a real broker client when execute is off")

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.alpaca_paper_crypto.AlpacaPaperCryptoClient", _boom)

    session = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)
    assert result["phase"] == "holding"


def test_tick_closes_a_position_on_close_intent(monkeypatch):
    close_strategy_code = """
def decide_crypto(as_of, positions, quotes, account):
    if not positions:
        return [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.005}]
    return [{"action": "close", "symbol": positions[0]["symbol"], "side": "sell", "qty": positions[0]["qty"]}]
"""
    strategy = _create_approved_strategy(code=close_strategy_code, interval=5)
    monkeypatch.setattr(engine, "get_crypto_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))

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
    """1 whole BTC at a real price is ~$77,000 against a $1,000 wallet."""
    strategy = _create_approved_strategy(code="""
def decide_crypto(as_of, positions, quotes, account):
    if positions:
        return []
    return [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 1}]
""")
    monkeypatch.setattr(engine, "get_crypto_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))

    session = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session)

    assert result["orders"] == 0
    positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert positions == []


def test_tick_accounts_for_capital_already_committed_to_open_positions(monkeypatch):
    code = """
def decide_crypto(as_of, positions, quotes, account):
    return [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.008}]
"""
    strategy = _create_approved_strategy(code=code, interval=5)
    monkeypatch.setattr(engine, "get_crypto_quotes_batch", lambda symbols: dict(_FAKE_QUOTES))

    session1 = _FakeSession(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc))
    result1 = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session1)
    assert result1["orders"] == 1  # 0.008 * 77000 = $616 fits in $1,000

    session2 = _FakeSession(datetime(2026, 8, 23, 15, 10, tzinfo=timezone.utc))
    result2 = engine.tick_uploaded_strategy(TRADING_DATE, strategy["key"], strategy, session2)
    assert result2["orders"] == 0  # a second $616 position would need $1,232 total, only $384 remains

    positions = repo.list_positions(TRADING_DATE, strategy["key"], bucket="paper", status="open")
    assert len(positions) == 1
