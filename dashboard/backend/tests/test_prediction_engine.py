"""Tests for domain/prediction/engine.py -- the forward-tick mechanic.
Market data (Kalshi/Polymarket) is mocked throughout; only the engine's own
logic (normalization, fills, fee application, cash cap) is under test.
"""

from __future__ import annotations

import pytest

from dashboard.backend.domain.prediction import engine, repository as repo

repo.init_schema()

_KALSHI_MARKET = {
    "ticker": "KXFOO-99", "event_title": "Will foo happen?",
    "yes_bid_dollars": "0.4000", "yes_ask_dollars": "0.4200",
    "last_price_dollars": "0.4000", "close_time": "2026-12-31T00:00:00Z",
}

_POLYMARKET_MARKET = {
    "question": "Will bar happen?", "conditionId": "0xabc",
    "outcome_prices": {"Yes": 0.30, "No": 0.70}, "endDate": "2026-12-31",
}


def test_fetch_active_markets_normalizes_kalshi_and_polymarket(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.kalshi_markets.list_active_markets",
        lambda limit=20: [_KALSHI_MARKET],
    )
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.polymarket_markets.list_active_markets",
        lambda limit=20: [_POLYMARKET_MARKET],
    )
    markets = engine.fetch_active_markets(limit=10)
    assert len(markets) == 2
    kalshi = next(m for m in markets if m["platform"] == "kalshi")
    assert kalshi["market_id"] == "KXFOO-99"
    assert kalshi["title"] == "Will foo happen?"
    outcome_names = {o["name"] for o in kalshi["outcomes"]}
    assert outcome_names == {"yes", "no"}
    yes_price = next(o["price"] for o in kalshi["outcomes"] if o["name"] == "yes")
    assert yes_price == pytest.approx(0.40)
    no_price = next(o["price"] for o in kalshi["outcomes"] if o["name"] == "no")
    assert no_price == pytest.approx(0.60)

    poly = next(m for m in markets if m["platform"] == "polymarket")
    assert poly["market_id"] == "0xabc"
    assert {o["name"] for o in poly["outcomes"]} == {"Yes", "No"}


def test_one_platform_failing_does_not_sink_the_other(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.kalshi_markets.list_active_markets", broken,
    )
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.polymarket_markets.list_active_markets",
        lambda limit=20: [_POLYMARKET_MARKET],
    )
    markets = engine.fetch_active_markets(limit=10)
    assert len(markets) == 1
    assert markets[0]["platform"] == "polymarket"


def test_apply_intents_opens_a_position_and_charges_fees():
    markets_by_id = {("kalshi", "KXFOO-99"): {
        "platform": "kalshi", "market_id": "KXFOO-99",
        "outcomes": [{"name": "yes", "price": 0.5}, {"name": "no", "price": 0.5}],
    }}
    intents = [{"action": "open", "platform": "kalshi", "market_id": "KXFOO-99", "outcome": "yes", "side": "buy", "qty": 10}]
    cash, positions, fees_paid = engine._apply_intents(intents, markets_by_id, 1000.0, [])
    # Cost = 10 * 0.5 = 5.0; fee = kalshi_fee(10, 0.5) = ceil(0.07*10*0.25*100)/100 = 0.18
    assert cash == pytest.approx(1000.0 - 5.0 - 0.18)
    assert len(positions) == 1
    assert fees_paid == pytest.approx(0.18)


def test_apply_intents_refuses_an_open_that_would_exceed_cash():
    markets_by_id = {("kalshi", "KXFOO-99"): {
        "platform": "kalshi", "market_id": "KXFOO-99",
        "outcomes": [{"name": "yes", "price": 0.9}],
    }}
    intents = [{"action": "open", "platform": "kalshi", "market_id": "KXFOO-99", "outcome": "yes", "side": "buy", "qty": 1000}]
    cash, positions, fees_paid = engine._apply_intents(intents, markets_by_id, 10.0, [])
    assert cash == 10.0
    assert positions == []
    assert fees_paid == 0.0


def test_apply_intents_closes_a_position():
    markets_by_id = {("kalshi", "KXFOO-99"): {
        "platform": "kalshi", "market_id": "KXFOO-99",
        "outcomes": [{"name": "yes", "price": 0.6}],
    }}
    existing = [{"platform": "kalshi", "market_id": "KXFOO-99", "outcome": "yes", "side": "buy", "qty": 10, "entry_price": 0.5}]
    intents = [{"action": "close", "platform": "kalshi", "market_id": "KXFOO-99", "outcome": "yes", "side": "buy", "qty": 10}]
    cash, positions, fees_paid = engine._apply_intents(intents, markets_by_id, 995.0, existing)
    assert positions == []
    # Proceeds = 10 * 0.6 = 6.0, minus the close fee.
    assert cash == pytest.approx(995.0 + 6.0 - fees_paid)


def test_tick_all_advances_a_manual_strategy_one_day(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.kalshi_markets.list_active_markets",
        lambda limit=20: [_KALSHI_MARKET],
    )
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.polymarket_markets.list_active_markets",
        lambda limit=20: [],
    )
    code = """
def decide_prediction(as_of, positions, markets, account):
    return []
"""
    row = repo.create(name="Do Nothing", description="", source_type="manual", code=code)
    # tick_all() advances every waiting strategy in the table, not just this
    # test's own -- other test modules in this same run share the DB (per
    # conftest.py's single temp DATABASE_PATH), so assert on this row
    # specifically rather than the total count.
    updated = engine.tick_all(as_of="2026-10-01")
    mine = next(r for r in updated if r["id"] == row["id"])
    assert mine["day_count"] == 1
    assert mine["status"] == "waiting"
    assert mine["equity_curve"] == [{"date": "2026-10-01", "equity": pytest.approx(1000.0)}]


def test_tick_all_is_idempotent_within_the_same_day(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.kalshi_markets.list_active_markets",
        lambda limit=20: [],
    )
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.polymarket_markets.list_active_markets",
        lambda limit=20: [],
    )
    code = "def decide_prediction(as_of, positions, markets, account):\n    return []\n"
    row = repo.create(name="Idempotent", description="", source_type="manual", code=code)
    first = engine.tick_all(as_of="2026-11-01")
    second = engine.tick_all(as_of="2026-11-01")
    assert any(r["id"] == row["id"] for r in first)
    assert not any(r["id"] == row["id"] for r in second)


def test_tick_all_catches_a_strategy_error_without_sinking_others(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.kalshi_markets.list_active_markets",
        lambda limit=20: [],
    )
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.market_data.polymarket_markets.list_active_markets",
        lambda limit=20: [],
    )
    broken = repo.create(name="Broken", description="", source_type="manual", code="not valid python (((")
    fine_code = "def decide_prediction(as_of, positions, markets, account):\n    return []\n"
    fine = repo.create(name="Fine", description="", source_type="manual", code=fine_code)

    updated = engine.tick_all(as_of="2026-12-01")
    fine_result = next((r for r in updated if r["id"] == fine["id"]), None)
    assert fine_result is not None
    assert fine_result["status"] == "waiting"
    # The broken one's sandbox call degrades to "no orders" (syntax error
    # inside the subprocess), not an engine-level exception -- it still
    # ticks forward like any other strategy with an empty decision.
    broken_result = repo.get(broken["id"])
    assert broken_result["status"] in ("waiting", "error")


# ---------------------------------------------------------------------------
# Real Kalshi execution: alongside the local simulated fill, best-effort,
# only for a strategy whose owner has connected demo credentials, never for
# Polymarket (no demo exchange exists -- see polymarket_paper.py's docstring).
# ---------------------------------------------------------------------------

_KALSHI_MARKETS_BY_ID = {("kalshi", "KXFOO-99"): {
    "platform": "kalshi", "market_id": "KXFOO-99",
    "outcomes": [{"name": "yes", "price": 0.5}, {"name": "no", "price": 0.5}],
}}
_OPEN_INTENT = {"action": "open", "platform": "kalshi", "market_id": "KXFOO-99", "outcome": "yes", "side": "buy", "qty": 10}


def test_no_user_id_never_attempts_a_real_order(monkeypatch):
    monkeypatch.setattr(
        engine, "_place_real_kalshi_order",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called with no user_id")),
    )
    cash, positions, fees_paid = engine._apply_intents([_OPEN_INTENT], _KALSHI_MARKETS_BY_ID, 1000.0, [], user_id=None)
    assert len(positions) == 1  # the simulated fill still happens


def test_no_connected_credentials_never_attempts_a_real_order(monkeypatch):
    monkeypatch.setattr(engine, "_kalshi_credentials_for", lambda user_id: None)
    monkeypatch.setattr(
        engine, "_place_real_kalshi_order",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    cash, positions, fees_paid = engine._apply_intents([_OPEN_INTENT], _KALSHI_MARKETS_BY_ID, 1000.0, [], user_id=7)
    assert len(positions) == 1


def test_connected_credentials_triggers_a_real_order_attempt(monkeypatch):
    monkeypatch.setattr(engine, "_kalshi_credentials_for", lambda user_id: {"api_key": "k", "secret_key": "pem"})
    calls = []
    monkeypatch.setattr(engine, "_place_real_kalshi_order", lambda intent, creds: calls.append(intent) or True)
    cash, positions, fees_paid = engine._apply_intents([_OPEN_INTENT], _KALSHI_MARKETS_BY_ID, 1000.0, [], user_id=7)
    assert len(calls) == 1
    assert calls[0]["market_id"] == "KXFOO-99"
    assert len(positions) == 1, "the simulated fill happens regardless of the real order attempt"


def test_polymarket_intents_never_route_through_the_kalshi_real_order_path(monkeypatch):
    # Platform-routing correctness, not "Polymarket never places a real
    # order" (it does now -- see the double-gated tests further down): a
    # Polymarket intent must never accidentally trigger a Kalshi order call
    # just because Kalshi credentials happen to also be connected.
    monkeypatch.setattr(engine, "_kalshi_credentials_for", lambda user_id: {"api_key": "k", "secret_key": "pem"})
    monkeypatch.setattr(
        engine, "_place_real_kalshi_order",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Polymarket must never reach the Kalshi path")),
    )
    poly_market = {("polymarket", "0xabc"): {
        "platform": "polymarket", "market_id": "0xabc", "outcomes": [{"name": "Yes", "price": 0.5}],
    }}
    poly_intent = {"action": "open", "platform": "polymarket", "market_id": "0xabc", "outcome": "Yes", "side": "buy", "qty": 10}
    cash, positions, fees_paid = engine._apply_intents([poly_intent], poly_market, 1000.0, [], user_id=7)
    assert len(positions) == 1


def test_a_failed_real_order_does_not_block_the_simulated_fill(monkeypatch):
    monkeypatch.setattr(engine, "_kalshi_credentials_for", lambda user_id: {"api_key": "k", "secret_key": "pem"})
    monkeypatch.setattr(engine, "_place_real_kalshi_order", lambda intent, creds: False)
    cash, positions, fees_paid = engine._apply_intents([_OPEN_INTENT], _KALSHI_MARKETS_BY_ID, 1000.0, [], user_id=7)
    assert len(positions) == 1
    assert cash < 1000.0  # the simulated debit still happened


def test_place_real_kalshi_order_maps_outcome_and_side_correctly(monkeypatch):
    captured = {}

    class _FakeKalshiClient:
        def __init__(self, credentials, environment):
            captured["environment"] = environment
            captured["credentials"] = credentials

        def place_order(self, *, ticker, side, action, count):
            captured.update(ticker=ticker, side=side, action=action, count=count)
            return {"order": {"order_id": "abc"}}

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.kalshi_paper.KalshiClient", _FakeKalshiClient)
    result = engine._place_real_kalshi_order(_OPEN_INTENT, {"api_key": "key-1", "secret_key": "pem-1"})
    assert result is True
    assert captured["environment"] == "demo", "must never default to production"
    assert captured["ticker"] == "KXFOO-99"
    assert captured["side"] == "yes"  # from intent["outcome"]
    assert captured["action"] == "buy"  # from intent["side"]
    assert captured["count"] == 10


def test_place_real_kalshi_order_swallows_a_rejected_order(monkeypatch):
    from dashboard.backend.infrastructure.brokers.kalshi_paper import KalshiOrderError

    class _RejectingClient:
        def __init__(self, credentials, environment):
            pass

        def place_order(self, **kwargs):
            raise KalshiOrderError("insufficient balance")

    monkeypatch.setattr("dashboard.backend.infrastructure.brokers.kalshi_paper.KalshiClient", _RejectingClient)
    result = engine._place_real_kalshi_order(_OPEN_INTENT, {"api_key": "key-1", "secret_key": "pem-1"})
    assert result is False


def test_place_real_kalshi_order_swallows_a_malformed_pem(monkeypatch):
    # A garbage "secret_key" (not a real PEM) must degrade to "not placed,"
    # not propagate a cryptography exception into the tick loop.
    result = engine._place_real_kalshi_order(_OPEN_INTENT, {"api_key": "key-1", "secret_key": "not a real pem"})
    assert result is False


# ---------------------------------------------------------------------------
# Real Polymarket execution: the double-gate (connected wallet key AND the
# operator-level POLYMARKET_EXECUTE flag) -- real money, no demo floor, so
# this is the one real-order path in the engine with a second gate.
# ---------------------------------------------------------------------------

_POLY_MARKETS_BY_ID = {("polymarket", "0xabc"): {
    "platform": "polymarket", "market_id": "0xabc", "outcomes": [{"name": "Yes", "price": 0.5}],
}}
_POLY_OPEN_INTENT = {"action": "open", "platform": "polymarket", "market_id": "0xabc", "outcome": "Yes", "side": "buy", "qty": 10}


def test_polymarket_credentials_connected_but_execute_disabled_never_places_a_real_order(monkeypatch):
    monkeypatch.setattr(engine, "_polymarket_credentials_for", lambda user_id: {"api_key": "0xkey"})
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.brokers.polymarket_paper.execute_enabled", lambda: False,
    )
    cash, positions, fees_paid = engine._apply_intents([_POLY_OPEN_INTENT], _POLY_MARKETS_BY_ID, 1000.0, [], user_id=7)
    assert len(positions) == 1  # simulated fill still happens


def test_polymarket_credentials_and_execute_enabled_attempts_a_real_order(monkeypatch):
    monkeypatch.setattr(engine, "_polymarket_credentials_for", lambda user_id: {"api_key": "0xkey"})
    calls = []
    monkeypatch.setattr(engine, "_place_real_polymarket_order", lambda intent, price, creds: calls.append(intent) or True)
    cash, positions, fees_paid = engine._apply_intents([_POLY_OPEN_INTENT], _POLY_MARKETS_BY_ID, 1000.0, [], user_id=7)
    assert len(calls) == 1
    assert calls[0]["market_id"] == "0xabc"
    assert len(positions) == 1


def test_kalshi_intents_never_reach_the_polymarket_path(monkeypatch):
    monkeypatch.setattr(engine, "_kalshi_credentials_for", lambda user_id: None)
    monkeypatch.setattr(engine, "_polymarket_credentials_for", lambda user_id: {"api_key": "0xkey"})
    monkeypatch.setattr(
        engine, "_place_real_polymarket_order",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Kalshi intent must never reach the Polymarket path")),
    )
    cash, positions, fees_paid = engine._apply_intents([_OPEN_INTENT], _KALSHI_MARKETS_BY_ID, 1000.0, [], user_id=7)
    assert len(positions) == 1


def test_place_real_polymarket_order_respects_the_execute_gate(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.brokers.polymarket_paper.execute_enabled", lambda: False,
    )
    result = engine._place_real_polymarket_order(_POLY_OPEN_INTENT, 0.5, {"api_key": "0xkey"})
    assert result is False


def test_place_real_polymarket_order_maps_fields_and_submits(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.brokers.polymarket_paper.execute_enabled", lambda: True,
    )
    captured = {}

    class _FakeClient:
        def __init__(self, credentials):
            captured["wallet_key"] = credentials.wallet_private_key

        def place_order(self, *, token_id, side, size, price):
            captured.update(token_id=token_id, side=side, size=size, price=price)
            return {"orderID": "abc"}

    monkeypatch.setattr(
        "dashboard.backend.infrastructure.brokers.polymarket_paper.PolymarketClient", _FakeClient,
    )
    result = engine._place_real_polymarket_order(_POLY_OPEN_INTENT, 0.5, {"api_key": "0xdeadbeef"})
    assert result is True
    assert captured["wallet_key"] == "0xdeadbeef"
    assert captured["token_id"] == "0xabc"
    assert captured["side"] == "buy"
    assert captured["size"] == 10
    assert captured["price"] == 0.5


def test_place_real_polymarket_order_swallows_a_rejected_order(monkeypatch):
    from dashboard.backend.infrastructure.brokers.polymarket_paper import PolymarketOrderError

    monkeypatch.setattr(
        "dashboard.backend.infrastructure.brokers.polymarket_paper.execute_enabled", lambda: True,
    )

    class _RejectingClient:
        def __init__(self, credentials):
            pass

        def place_order(self, **kwargs):
            raise PolymarketOrderError("insufficient balance")

    monkeypatch.setattr(
        "dashboard.backend.infrastructure.brokers.polymarket_paper.PolymarketClient", _RejectingClient,
    )
    result = engine._place_real_polymarket_order(_POLY_OPEN_INTENT, 0.5, {"api_key": "0xdeadbeef"})
    assert result is False
