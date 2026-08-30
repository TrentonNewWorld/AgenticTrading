"""infrastructure/brokers/alpaca_paper_crypto.py's order-building logic --
market orders, fractional qty, TimeInForce.GTC. Mocks the SDK entirely; no
network access (even though, unlike Tradovate/OANDA, this broker connection
was genuinely spike-verified working end to end against a real paper
account -- see that module's docstring -- a unit test still must never place
a real order).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dashboard.backend.infrastructure.brokers.alpaca_paper_crypto import AlpacaPaperCryptoClient


@pytest.fixture
def client(monkeypatch):
    fake_trading_client = MagicMock()
    monkeypatch.setattr("alpaca.trading.client.TradingClient", lambda *a, **k: fake_trading_client)
    client = AlpacaPaperCryptoClient(api_key="key", secret_key="secret")
    return client, fake_trading_client


def _fake_order(order_id="order-1", status="accepted"):
    order = MagicMock()
    order.id = order_id
    order.status = status
    return order


def test_buy_order_uses_market_order_with_gtc(client):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    c, trading = client
    trading.submit_order.return_value = _fake_order()

    result = c.submit_crypto_order(symbol="BTC/USD", side="buy", qty=0.0025)

    assert result is not None
    assert result.order_id == "order-1"
    submitted = trading.submit_order.call_args.kwargs["order_data"]
    assert isinstance(submitted, MarketOrderRequest)
    assert submitted.symbol == "BTC/USD"
    assert submitted.side == OrderSide.BUY
    assert submitted.time_in_force == TimeInForce.GTC
    assert float(submitted.qty) == pytest.approx(0.0025)


def test_sell_order_uses_sell_side(client):
    from alpaca.trading.enums import OrderSide

    c, trading = client
    trading.submit_order.return_value = _fake_order()

    c.submit_crypto_order(symbol="ETH/USD", side="sell", qty=0.5)

    submitted = trading.submit_order.call_args.kwargs["order_data"]
    assert submitted.side == OrderSide.SELL


def test_qty_supports_fractional_coins(client):
    """The one deliberate deviation from the futures/forex brokers -- a
    real BTC position sized to a $1,000 wallet is necessarily fractional
    (see alpaca_paper_crypto.py's docstring)."""
    c, trading = client
    trading.submit_order.return_value = _fake_order()

    c.submit_crypto_order(symbol="BTC/USD", side="buy", qty=0.00194805)

    submitted = trading.submit_order.call_args.kwargs["order_data"]
    assert float(submitted.qty) == pytest.approx(0.00194805)


def test_rejects_invalid_side(client):
    c, _trading = client
    with pytest.raises(ValueError):
        c.submit_crypto_order(symbol="BTC/USD", side="hold", qty=0.001)


def test_rejects_zero_or_negative_qty(client):
    c, _trading = client
    with pytest.raises(ValueError):
        c.submit_crypto_order(symbol="BTC/USD", side="buy", qty=0)
    with pytest.raises(ValueError):
        c.submit_crypto_order(symbol="BTC/USD", side="buy", qty=-0.001)


def test_submit_failure_returns_none_rather_than_raising(client):
    """Mirrors alpaca_paper_options.py's own posture: a broker-side
    exception during submission is caught and logged, not propagated -- the
    caller (domain/crypto/engine.py) treats a None result as "the real
    order failed" without crashing the tick."""
    c, trading = client
    trading.submit_order.side_effect = RuntimeError("boom")

    result = c.submit_crypto_order(symbol="BTC/USD", side="buy", qty=0.001)
    assert result is None


def test_get_account_returns_crypto_status(client):
    c, trading = client
    account = MagicMock()
    account.cash = "1000.00"
    account.equity = "1000.00"
    account.buying_power = "2000.00"
    account.crypto_status = "ACTIVE"
    trading.get_account.return_value = account

    result = c.get_account()
    assert result["cash"] == 1000.0
    assert result["crypto_status"] == "ACTIVE"


def test_cancel_order_returns_true_on_success(client):
    c, trading = client
    assert c.cancel_order("order-1") is True
    trading.cancel_order_by_id.assert_called_once_with("order-1")


def test_cancel_order_returns_false_on_failure(client):
    c, trading = client
    trading.cancel_order_by_id.side_effect = RuntimeError("not found")
    assert c.cancel_order("order-1") is False
