"""Tests for infrastructure/brokers/polymarket_paper.py.

Order construction/signing goes through Polymarket's own official
py-clob-client library (see that module's docstring for why, instead of
hand-rolled EIP-712 for the CTF Exchange order struct). This session
independently verified client construction, tick-size resolution against a
real live market, and full EIP-712 order signing all succeed end to end
using a well-known deterministic test private key (never used for anything
real) -- only the final network submit was not exercised (needs a funded
wallet). These tests cover this module's own logic: input validation,
credential resolution, the execute_enabled() gate, and exception wrapping
around a mocked ClobClient (so they run with no network and no real key).
"""

from __future__ import annotations

import pytest

from dashboard.backend.infrastructure.brokers.polymarket_paper import (
    PolymarketClient,
    PolymarketConfigError,
    PolymarketCredentials,
    PolymarketOrderError,
    credentials_from_env,
    execute_enabled,
)

# A well-known, publicly documented Ethereum test private key (Ethereum's
# canonical "key 1" test vector) -- never used for anything real, safe to
# hardcode in a test file.
TEST_PRIVATE_KEY = "0x" + "1".zfill(64)
TEST_ADDRESS = "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"


def test_credentials_from_env_requires_the_key(monkeypatch):
    monkeypatch.delenv("POLYMARKET_WALLET_PRIVATE_KEY", raising=False)
    with pytest.raises(PolymarketConfigError):
        credentials_from_env()


def test_credentials_from_env_reads_the_key(monkeypatch):
    monkeypatch.setenv("POLYMARKET_WALLET_PRIVATE_KEY", TEST_PRIVATE_KEY)
    creds = credentials_from_env()
    assert creds.wallet_private_key == TEST_PRIVATE_KEY


def test_execute_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("POLYMARKET_EXECUTE", raising=False)
    assert execute_enabled() is False


def test_execute_enabled_reads_fresh_not_cached(monkeypatch):
    monkeypatch.delenv("POLYMARKET_EXECUTE", raising=False)
    assert execute_enabled() is False
    monkeypatch.setenv("POLYMARKET_EXECUTE", "true")
    assert execute_enabled() is True


def _client():
    return PolymarketClient(credentials=PolymarketCredentials(wallet_private_key=TEST_PRIVATE_KEY))


def test_place_order_rejects_invalid_side():
    with pytest.raises(ValueError):
        _client().place_order(token_id="123", side="hold", size=10.0, price=0.5)


def test_place_order_rejects_non_positive_size():
    with pytest.raises(ValueError):
        _client().place_order(token_id="123", side="buy", size=0, price=0.5)


def test_place_order_rejects_price_out_of_bounds():
    with pytest.raises(ValueError):
        _client().place_order(token_id="123", side="buy", size=10.0, price=1.0)
    with pytest.raises(ValueError):
        _client().place_order(token_id="123", side="buy", size=10.0, price=0.0)


class _FakeSignedOrder:
    pass


class _FakeClobClient:
    def __init__(self, host, key=None, chain_id=None):
        self.host = host
        self.key = key
        self.chain_id = chain_id
        self._creds = None

    def create_or_derive_api_creds(self, nonce=None):
        return "fake-creds"

    def set_api_creds(self, creds):
        self._creds = creds

    def create_order(self, order_args):
        assert order_args.price == 0.05
        assert order_args.size == 10.0
        assert order_args.token_id == "123"
        return _FakeSignedOrder()

    def post_order(self, signed_order, orderType="GTC", post_only=False):
        assert isinstance(signed_order, _FakeSignedOrder)
        return {"orderID": "abc123", "success": True}


def test_place_order_builds_and_submits_through_the_official_client(monkeypatch):
    monkeypatch.setattr("py_clob_client.client.ClobClient", _FakeClobClient)
    result = _client().place_order(token_id="123", side="buy", size=10.0, price=0.05)
    assert result == {"orderID": "abc123", "success": True}


def test_place_order_reuses_the_constructed_client_across_calls(monkeypatch):
    construct_calls = []
    real_init = _FakeClobClient.__init__

    def counting_init(self, *a, **k):
        construct_calls.append(1)
        real_init(self, *a, **k)

    monkeypatch.setattr(_FakeClobClient, "__init__", counting_init)
    monkeypatch.setattr("py_clob_client.client.ClobClient", _FakeClobClient)
    client = _client()
    client.place_order(token_id="123", side="buy", size=10.0, price=0.05)
    client.place_order(token_id="123", side="buy", size=10.0, price=0.05)
    assert len(construct_calls) == 1, "the ClobClient should be constructed once, not per order"


class _RejectingClobClient(_FakeClobClient):
    def create_order(self, order_args):
        from py_clob_client.exceptions import PolyApiException

        class _FakeResp:
            status_code = 400
            text = '{"error": "insufficient balance"}'

            def json(self):
                return {"error": "insufficient balance"}

        raise PolyApiException(_FakeResp())


def test_place_order_wraps_a_rejected_order(monkeypatch):
    monkeypatch.setattr("py_clob_client.client.ClobClient", _RejectingClobClient)
    with pytest.raises(PolymarketOrderError):
        _client().place_order(token_id="123", side="buy", size=10.0, price=0.05)


class _BrokenClobClient(_FakeClobClient):
    def create_order(self, order_args):
        raise RuntimeError("network timeout")


def test_place_order_wraps_an_unexpected_failure(monkeypatch):
    monkeypatch.setattr("py_clob_client.client.ClobClient", _BrokenClobClient)
    with pytest.raises(PolymarketOrderError):
        _client().place_order(token_id="123", side="buy", size=10.0, price=0.05)


def test_signing_round_trips_against_a_known_test_vector():
    """Independent of this module's own mocked tests above: confirms the
    underlying eth-account dependency this whole client relies on (via
    py-clob-client) derives the expected address from a known private key --
    the same check performed live against Polymarket's real API this
    session (see the module docstring)."""
    from eth_account import Account

    acct = Account.from_key(TEST_PRIVATE_KEY)
    assert acct.address == TEST_ADDRESS
