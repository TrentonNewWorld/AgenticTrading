"""End-to-end test of the Manual page's daily state machine (engine.tick()),
driven entirely by fakes -- no real Alpaca calls, no real market clock. Walks
a full simulated trading day for the built-in Top 10 strategy: pending ->
screening -> holding -> closing -> closed, checking the promotion checkpoint
and the close-out/straggler sweep.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from dashboard.backend.domain.manual10 import engine, market_clock, repository as repo
from dashboard.backend.domain.manual10.repository import TOP_10_STRATEGY_KEY

TRADING_DATE = "2026-08-21"


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    db_path = tempfile.mktemp(suffix=".db")
    import dashboard.backend.domain.manual10.repository as repo_module
    monkeypatch.setattr(repo_module, "DB_PATH", db_path)
    repo_module._init_schema()
    yield


@pytest.fixture(autouse=True)
def _no_real_money(monkeypatch):
    # Belt and suspenders: even though the fake clients below never place a
    # real order, assert the engine's own gates stay at their safe defaults
    # for this whole test file.
    monkeypatch.delenv("ALPACA_PAPER_EXECUTE", raising=False)
    monkeypatch.delenv("ALPACA_LIVE_EXECUTE", raising=False)
    monkeypatch.delenv("MANUAL10_ALLOW_REAL_MONEY", raising=False)


@pytest.fixture(autouse=True)
def _clean_leaderboard():
    """engine.tick() now writes to domain/leaderboard/real_trading.py's
    ledger whenever Top 10 holds an open position -- that module
    deliberately shares the session-wide DATABASE_PATH (not manual10's own
    per-test-isolated DB above, since it's a cross-cutting ledger, not
    manual10-owned data), so ANY test in this file that ticks a real
    position (not just the tests written specifically to check this) must
    not leak a "top_10"/"top_10_live" row into the shared DB for every
    later test -- in this file or any other -- to trip over."""
    yield
    from dashboard.backend.domain.leaderboard import real_trading

    conn = real_trading._connect()
    try:
        for key in (engine.TOP10_PAPER_LEADERBOARD_KEY, engine.TOP10_LIVE_LEADERBOARD_KEY):
            conn.execute("DELETE FROM real_trading_equity_points WHERE strategy_key = ?", (key,))
            conn.execute("DELETE FROM real_trading_holdings WHERE strategy_key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


class _FakeSession:
    def __init__(self, now, open_at, close_at, has_session=True):
        self.now = now
        self.open_at = open_at
        self.close_at = close_at
        self.has_session = has_session
        self.trading_date = open_at.date() if open_at else now.date()


class _FakeBroker:
    """Stands in for both AlpacaPaperTradingClient and AlpacaLiveTradingClient
    -- both expose get_quotes/submit_market_order/get_account with the same
    shapes engine.py actually calls."""

    def __init__(self, prices, equity=5000.0):
        self.prices = dict(prices)
        self.equity = equity
        self.orders = []

    def get_quotes(self, symbols):
        return {s: self.prices[s] for s in symbols if s in self.prices}

    def submit_market_order(self, symbol, qty, side):
        self.orders.append((symbol, qty, side))
        return type("Order", (), {"order_id": f"order_{len(self.orders)}"})()

    def get_account(self):
        return {"cash": self.equity, "equity": self.equity, "buying_power": self.equity, "portfolio_value": self.equity}


@pytest.fixture
def fakes(monkeypatch):
    open_at = datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc)  # 9:30 ET
    close_at = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)  # 16:00 ET

    prices = {
        "AAAA": 10.0,   # will "grow" for promotion
        "BBBB": 20.0,   # will "shrink" -- stays in paper
        "CCCC": 5.0,
        "DDDD": 6.0,
        "EEEE": 7.0,
        "FFFF": 8.0,
        "GGGG": 9.0,
        "HHHH": 11.0,
        "IIII": 12.0,
        "JJJJ": 13.0,
    }
    paper_broker = _FakeBroker(prices)
    live_broker = _FakeBroker(prices, equity=12345.0)

    monkeypatch.setattr(engine, "_paper_client", lambda: paper_broker)
    monkeypatch.setattr(engine, "_live_client", lambda: live_broker)

    def fake_find_top_movers(*, top_n, price_min, price_max, client=None):
        return [
            {"symbol": sym, "latest_price": px, "change_pct": 10.0 + i}
            for i, (sym, px) in enumerate(prices.items())
        ][:top_n]

    monkeypatch.setattr(engine, "find_top_movers", fake_find_top_movers)

    state = {"now": open_at - timedelta(minutes=5)}

    def fake_get_today_session(today=None):
        return _FakeSession(state["now"], open_at, close_at)

    monkeypatch.setattr(market_clock, "get_today_session", fake_get_today_session)
    monkeypatch.setattr(engine.market_clock, "get_today_session", fake_get_today_session)
    # Every repository write that stamps a timestamp (entry_time, exit_time,
    # etc.) must move with the fake market clock too, or a promotion
    # checkpoint compares fake "now" against a real wall-clock entry_time
    # from whenever this test happened to run.
    monkeypatch.setattr(repo, "_now", lambda: state["now"])

    # tick() only drives strategies the user has explicitly activated for the
    # day -- selecting a strategy alone (without this) would leave it inert.
    repo.set_activated(TRADING_DATE, TOP_10_STRATEGY_KEY, True)

    return {
        "state": state, "open_at": open_at, "close_at": close_at,
        "paper_broker": paper_broker, "live_broker": live_broker, "prices": prices,
    }


def _advance(fakes, minutes):
    fakes["state"]["now"] += timedelta(minutes=minutes)


def _phase(result):
    return result["strategies"][TOP_10_STRATEGY_KEY]["phase"]


def test_full_day_walks_every_phase(fakes):
    # Before open: pending, and the wallet snapshot already happened.
    result = engine.tick()
    assert _phase(result) == "pending"
    day = repo.get_day(TRADING_DATE, TOP_10_STRATEGY_KEY)
    assert day["wallet_reset_amount"] == 12345.0  # snapshot of the *live* account's equity

    # Move into the screening window (settings default to a 10-minute window).
    _advance(fakes, 6)
    result = engine.tick()
    assert _phase(result) == "screening"
    candidates = repo.list_candidates(TRADING_DATE, TOP_10_STRATEGY_KEY)
    assert len(candidates) == 10
    assert all(c["picked"] == 0 for c in candidates)  # not finalized yet

    # Past the screener window: finalize + buy in paper.
    _advance(fakes, 10)  # now open_at + 11 min, past the 10-minute window
    result = engine.tick()
    assert _phase(result) == "holding"
    positions = repo.list_positions(TRADING_DATE, TOP_10_STRATEGY_KEY, bucket="paper", status="open")
    assert len(positions) == 10
    assert {p["symbol"] for p in positions} == set(fakes["prices"])
    # No real paper order was actually placed -- ALPACA_PAPER_EXECUTE is off.
    assert fakes["paper_broker"].orders == []

    # Promotion checkpoint: AAAA grows, BBBB shrinks.
    fakes["paper_broker"].prices["AAAA"] = 15.0  # up from entry 10.0
    fakes["paper_broker"].prices["BBBB"] = 15.0  # down from entry 20.0
    _advance(fakes, 6)  # promotion_window_minutes default is 6
    result = engine.tick()
    assert _phase(result) == "holding"

    all_positions = repo.list_positions(TRADING_DATE, TOP_10_STRATEGY_KEY)
    aaaa_paper = [p for p in all_positions if p["symbol"] == "AAAA" and p["bucket"] == "paper"]
    aaaa_real = [p for p in all_positions if p["symbol"] == "AAAA" and p["bucket"] == "real"]
    bbbb_paper = [p for p in all_positions if p["symbol"] == "BBBB" and p["bucket"] == "paper"]

    # AAAA grew, but with both real-money gates off it stays in paper with a note --
    # never fabricated as a real position.
    assert len(aaaa_real) == 0
    assert aaaa_paper[0]["promotion_checked"] == 1
    assert "disabled" in aaaa_paper[0]["promotion_note"]
    assert bbbb_paper[0]["promotion_checked"] == 1
    assert "not above entry" in bbbb_paper[0]["promotion_note"]
    # No live order was placed either way.
    assert fakes["live_broker"].orders == []

    # Fast-forward to the close-out window. One tick() cascades straight from
    # holding through closing to fully sold, since the fallthrough loop
    # catches a single call up through every phase boundary it's already
    # past -- not just the very next one.
    fakes["state"]["now"] = fakes["close_at"] - timedelta(minutes=25)
    result = engine.tick()
    assert _phase(result) == "closing"

    # Close-out closes every position even with execute off (the *state*
    # closes regardless -- placing a real/paper order is what's gated, not
    # recording the close in our own ledger).
    closed_now = repo.list_positions(TRADING_DATE, TOP_10_STRATEGY_KEY, status="closed")
    assert len(closed_now) == 10
    assert all(p["close_reason"] == "close_out" for p in closed_now)

    # Past the straggler checkpoint too, with nothing left open -> the day closes.
    fakes["state"]["now"] = fakes["close_at"] - timedelta(minutes=10)
    result = engine.tick()
    assert _phase(result) == "closed"
    final_day = repo.get_day(TRADING_DATE, TOP_10_STRATEGY_KEY)
    assert final_day["result"] in ("win", "loss", "flat")
    assert final_day["realized_pnl"] is not None


def test_unactivated_strategy_is_never_ticked(fakes):
    repo.set_activated(TRADING_DATE, TOP_10_STRATEGY_KEY, False)
    result = engine.tick()
    assert result["strategies"] == {}
    assert repo.get_day(TRADING_DATE, TOP_10_STRATEGY_KEY) is None


def test_promotion_actually_executes_with_both_gates_enabled(fakes, monkeypatch):
    monkeypatch.setenv("ALPACA_LIVE_EXECUTE", "true")
    monkeypatch.setenv("MANUAL10_ALLOW_REAL_MONEY", "true")

    engine.tick()  # pending -> wallet reset
    _advance(fakes, 6)
    engine.tick()  # screening
    _advance(fakes, 10)
    engine.tick()  # holding, bought in paper

    fakes["paper_broker"].prices["AAAA"] = 15.0  # up from entry 10.0
    _advance(fakes, 6)
    result = engine.tick()
    assert _phase(result) == "holding"

    all_positions = repo.list_positions(TRADING_DATE, TOP_10_STRATEGY_KEY)
    aaaa_real = [p for p in all_positions if p["symbol"] == "AAAA" and p["bucket"] == "real"]
    aaaa_paper_closed = [
        p for p in all_positions if p["symbol"] == "AAAA" and p["bucket"] == "paper" and p["status"] == "closed"
    ]
    assert len(aaaa_real) == 1
    assert aaaa_real[0]["promoted_from_paper"] == 1
    assert aaaa_real[0]["real_order_id"] == "order_1"
    assert len(aaaa_paper_closed) == 1
    assert aaaa_paper_closed[0]["close_reason"] == "promoted"
    # A real order was actually placed on the live broker.
    assert ("AAAA", pytest.approx(1.0), "buy") == fakes["live_broker"].orders[0]


def test_manual_sell_closes_a_paper_position_without_execute(fakes):
    pid = repo.open_position(
        trading_date=TRADING_DATE, strategy_key=TOP_10_STRATEGY_KEY, symbol="AAAA", bucket="paper",
        shares=1.0, entry_price=10.0,
    )
    fakes["paper_broker"].prices["AAAA"] = 12.0
    result = engine.manual_sell(pid)
    assert result["status"] == "closed"
    assert result["exit_price"] == 12.0
    assert result["close_reason"] == "manual_sell"
    assert fakes["paper_broker"].orders == []  # ALPACA_PAPER_EXECUTE is off


def test_manual_sell_rejects_already_closed_position(fakes):
    pid = repo.open_position(
        trading_date=TRADING_DATE, strategy_key=TOP_10_STRATEGY_KEY, symbol="AAAA", bucket="paper",
        shares=1.0, entry_price=10.0,
    )
    repo.close_position(pid, exit_price=11.0, close_reason="manual_sell")
    with pytest.raises(engine.ManualActionError, match="already closed"):
        engine.manual_sell(pid)


def test_manual_promote_requires_live_execute(fakes):
    pid = repo.open_position(
        trading_date=TRADING_DATE, strategy_key=TOP_10_STRATEGY_KEY, symbol="AAAA", bucket="paper",
        shares=1.0, entry_price=10.0,
    )
    with pytest.raises(engine.ManualActionError, match="disabled"):
        engine.manual_promote(pid)
    # Still open in paper -- a rejected promotion must not half-apply.
    assert repo.get_position(pid)["status"] == "open"


def test_manual_promote_succeeds_with_live_execute_only(fakes, monkeypatch):
    """Deliberately NOT setting MANUAL10_ALLOW_REAL_MONEY -- a human clicking
    the button is a different risk profile than the unattended scheduler, and
    only needs the account-wide switch every other manual live control uses."""
    monkeypatch.setenv("ALPACA_LIVE_EXECUTE", "true")
    pid = repo.open_position(
        trading_date=TRADING_DATE, strategy_key=TOP_10_STRATEGY_KEY, symbol="AAAA", bucket="paper",
        shares=1.0, entry_price=10.0,
    )
    fakes["live_broker"].prices["AAAA"] = 14.0

    new_position = engine.manual_promote(pid)
    assert new_position["bucket"] == "real"
    assert new_position["entry_price"] == 14.0
    assert new_position["promoted_from_paper"] == 1
    assert repo.get_position(pid)["status"] == "closed"
    assert repo.get_position(pid)["close_reason"] == "promoted"
    assert fakes["live_broker"].orders == [("AAAA", 1.0, "buy")]


def test_manual_demote_sells_real_and_reopens_in_paper(fakes, monkeypatch):
    monkeypatch.setenv("ALPACA_LIVE_EXECUTE", "true")
    pid = repo.open_position(
        trading_date=TRADING_DATE, strategy_key=TOP_10_STRATEGY_KEY, symbol="AAAA", bucket="real",
        shares=2.0, entry_price=10.0,
    )
    fakes["live_broker"].prices["AAAA"] = 9.0

    new_position = engine.manual_demote(pid)
    assert new_position["bucket"] == "paper"
    assert new_position["entry_price"] == 9.0
    assert repo.get_position(pid)["status"] == "closed"
    assert repo.get_position(pid)["close_reason"] == "demoted"
    assert fakes["live_broker"].orders == [("AAAA", 2.0, "sell")]


def test_top10_paper_position_feeds_the_real_trading_leaderboard(fakes):
    """Regression: Manual's Top 10 previously had no leaderboard footprint
    at all -- a user could activate it, watch it trade, and never see it
    show up anywhere alongside the Strategy Catalog's Run in Paper/Live
    entries on the Live Trading Leaderboard."""
    from dashboard.backend.domain.leaderboard import real_trading

    repo.open_position(
        trading_date=TRADING_DATE, strategy_key=TOP_10_STRATEGY_KEY, symbol="AAAA", bucket="paper",
        shares=2.0, entry_price=10.0,
    )
    fakes["paper_broker"].prices["AAAA"] = 12.0

    engine._record_top10_leaderboard_snapshot(TRADING_DATE)

    board = real_trading.get_real_trading_leaderboard()
    keys = {e["key"] for e in board["entries"]}
    assert engine.TOP10_PAPER_LEADERBOARD_KEY in keys
    assert engine.TOP10_LIVE_LEADERBOARD_KEY not in keys  # no real-bucket position exists yet


def test_top10_paper_and_live_buckets_get_independent_leaderboard_entries(fakes):
    from dashboard.backend.domain.leaderboard import real_trading

    repo.open_position(
        trading_date=TRADING_DATE, strategy_key=TOP_10_STRATEGY_KEY, symbol="AAAA", bucket="paper",
        shares=2.0, entry_price=10.0,
    )
    repo.open_position(
        trading_date=TRADING_DATE, strategy_key=TOP_10_STRATEGY_KEY, symbol="BBBB", bucket="real",
        shares=1.0, entry_price=20.0,
    )
    fakes["paper_broker"].prices["AAAA"] = 12.0
    fakes["live_broker"].prices["BBBB"] = 22.0

    engine._record_top10_leaderboard_snapshot(TRADING_DATE)

    board = real_trading.get_real_trading_leaderboard()
    entries = {e["key"]: e for e in board["entries"]}
    assert engine.TOP10_PAPER_LEADERBOARD_KEY in entries
    assert engine.TOP10_LIVE_LEADERBOARD_KEY in entries
    # Independent notional sub-portfolios -- the live entry's value must
    # reflect only BBBB, not get contaminated by the paper AAAA position.
    assert entries[engine.TOP10_LIVE_LEADERBOARD_KEY]["current_value"] > 0


def test_leaderboard_snapshot_is_a_noop_with_no_open_positions(fakes):
    from dashboard.backend.domain.leaderboard import real_trading

    engine._record_top10_leaderboard_snapshot(TRADING_DATE)

    board = real_trading.get_real_trading_leaderboard()
    keys = {e["key"] for e in board["entries"]}
    assert engine.TOP10_PAPER_LEADERBOARD_KEY not in keys
    assert engine.TOP10_LIVE_LEADERBOARD_KEY not in keys


def test_leaderboard_snapshot_failure_never_raises(fakes, monkeypatch):
    repo.open_position(
        trading_date=TRADING_DATE, strategy_key=TOP_10_STRATEGY_KEY, symbol="AAAA", bucket="paper",
        shares=2.0, entry_price=10.0,
    )

    def _boom(*a, **k):
        raise RuntimeError("db unavailable")

    from dashboard.backend.domain.leaderboard import real_trading
    monkeypatch.setattr(real_trading, "record_real_trading_snapshot", _boom)

    engine._record_top10_leaderboard_snapshot(TRADING_DATE)  # must not raise


def test_no_session_day_is_a_pure_noop(monkeypatch):
    def fake_no_session(today=None):
        return _FakeSession(now=datetime(2026, 8, 22, tzinfo=timezone.utc), open_at=None, close_at=None, has_session=False)

    monkeypatch.setattr(market_clock, "get_today_session", fake_no_session)
    monkeypatch.setattr(engine.market_clock, "get_today_session", fake_no_session)

    result = engine.tick()
    assert result["phase"] == "no_session"
    assert repo.get_day("2026-08-22", TOP_10_STRATEGY_KEY) is None  # never even creates a day row
