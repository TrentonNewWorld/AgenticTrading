"""Sub-phase 4 of the Options-dashboard plan: the options broker clients'
order-building logic -- single-leg vs multi-leg (MLEG), always limit orders
(never market, per the live spike's finding that Alpaca time-gates market
orders on the options book to trading hours). Mocks the SDK entirely; no
network access.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_client(monkeypatch, module_name: str, class_name: str):
    module = __import__(
        f"dashboard.backend.infrastructure.brokers.{module_name}", fromlist=[class_name]
    )
    cls = getattr(module, class_name)
    fake_trading_client = MagicMock()
    monkeypatch.setattr("alpaca.trading.client.TradingClient", lambda *a, **k: fake_trading_client)
    client = cls(api_key="key", secret_key="secret")
    return client, fake_trading_client


@pytest.fixture
def paper_client(monkeypatch):
    return _make_client(monkeypatch, "alpaca_paper_options", "AlpacaPaperOptionsClient")


@pytest.fixture
def live_client(monkeypatch):
    return _make_client(monkeypatch, "alpaca_live_options", "AlpacaLiveOptionsClient")


def _fake_order(order_id="order-1", status="accepted"):
    order = MagicMock()
    order.id = order_id
    order.status = status
    return order


def test_single_leg_order_uses_simple_order_class(paper_client):
    from dashboard.backend.infrastructure.brokers.alpaca_paper_options import OptionLeg
    from alpaca.trading.enums import OrderClass

    client, trading = paper_client
    trading.submit_order.return_value = _fake_order()

    result = client.submit_option_order(
        [OptionLeg(symbol="AAPL260918C00185000", side="buy")],
        limit_price=2.50,
    )

    assert result is not None
    assert result.order_id == "order-1"
    submitted_request = trading.submit_order.call_args.kwargs["order_data"]
    assert submitted_request.order_class == OrderClass.SIMPLE
    assert submitted_request.symbol == "AAPL260918C00185000"
    assert float(submitted_request.limit_price) == 2.50


def test_two_leg_covered_call_uses_mleg_order_class(paper_client):
    """A covered call: long the stock (not modeled here, only the options
    leg) + short 1 call -- the option-leg half of the combo submitted as a
    real 2-leg MLEG order, mirroring the vertical-spread shape confirmed
    working against a live paper account in the Sub-phase 1 spike."""
    from dashboard.backend.infrastructure.brokers.alpaca_paper_options import OptionLeg
    from alpaca.trading.enums import OrderClass

    client, trading = paper_client
    trading.submit_order.return_value = _fake_order()

    legs = [
        OptionLeg(symbol="AAPL260918C00180000", side="buy", position_intent="buy_to_open"),
        OptionLeg(symbol="AAPL260918C00190000", side="sell", position_intent="sell_to_open"),
    ]
    result = client.submit_option_order(legs, limit_price=1.25)

    assert result is not None
    assert result.legs == ["AAPL260918C00180000", "AAPL260918C00190000"]
    submitted_request = trading.submit_order.call_args.kwargs["order_data"]
    assert submitted_request.order_class == OrderClass.MLEG
    assert len(submitted_request.legs) == 2
    assert float(submitted_request.limit_price) == 1.25


def test_order_is_always_a_limit_order_never_market(paper_client):
    """Confirmed against a live account (Sub-phase 1 spike): a MARKET order
    on the options book is rejected outside trading hours, while a LIMIT
    order is accepted any time -- the engine must never submit a market
    order for options, regardless of qty or leg count."""
    from dashboard.backend.infrastructure.brokers.alpaca_paper_options import OptionLeg
    from alpaca.trading.requests import LimitOrderRequest

    client, trading = paper_client
    trading.submit_order.return_value = _fake_order()

    client.submit_option_order([OptionLeg(symbol="AAPL260918C00185000", side="buy")], limit_price=2.0)

    submitted_request = trading.submit_order.call_args.kwargs["order_data"]
    assert isinstance(submitted_request, LimitOrderRequest)


def test_rejects_zero_or_negative_limit_price(paper_client):
    from dashboard.backend.infrastructure.brokers.alpaca_paper_options import OptionLeg

    client, _trading = paper_client
    with pytest.raises(ValueError):
        client.submit_option_order([OptionLeg(symbol="AAPL260918C00185000", side="buy")], limit_price=0)
    with pytest.raises(ValueError):
        client.submit_option_order([OptionLeg(symbol="AAPL260918C00185000", side="buy")], limit_price=-1)


def test_rejects_empty_legs(paper_client):
    client, _trading = paper_client
    with pytest.raises(ValueError):
        client.submit_option_order([], limit_price=1.0)


def test_get_option_positions_filters_out_equity_symbols(paper_client, monkeypatch):
    """A position list containing both an OCC contract and (hypothetically)
    an equity ticker must return only the option -- get_option_positions is
    specifically the options-only view."""
    client, trading = paper_client

    option_position = MagicMock()
    option_position.symbol = "AAPL260918C00185000"
    option_position.qty = "1"
    option_position.avg_entry_price = "2.50"
    option_position.current_price = "2.75"
    option_position.market_value = "275.00"
    option_position.unrealized_pl = "25.00"
    option_position.side = "long"

    equity_position = MagicMock()
    equity_position.symbol = "AAPL"

    trading.get_all_positions.return_value = [option_position, equity_position]

    positions = client.get_option_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL260918C00185000"


def test_live_client_matches_paper_client_order_shape(live_client):
    """Same order-building logic on the live client -- the two must submit
    byte-identical request shapes, differing only in the account they hit."""
    from dashboard.backend.infrastructure.brokers.alpaca_paper_options import OptionLeg
    from alpaca.trading.enums import OrderClass

    client, trading = live_client
    trading.submit_order.return_value = _fake_order()

    legs = [
        OptionLeg(symbol="AAPL260918C00180000", side="buy"),
        OptionLeg(symbol="AAPL260918C00190000", side="sell"),
    ]
    client.submit_option_order(legs, limit_price=1.25)

    submitted_request = trading.submit_order.call_args.kwargs["order_data"]
    assert submitted_request.order_class == OrderClass.MLEG
    assert float(submitted_request.limit_price) == 1.25
