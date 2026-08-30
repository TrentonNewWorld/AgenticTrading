from __future__ import annotations

import pytest

from dashboard.backend.domain.crypto.sandbox import (
    CryptoStrategyCodeError,
    run_decide_crypto,
    validate_code,
)


def test_validate_code_accepts_a_well_formed_strategy():
    validate_code("""
def decide_crypto(as_of, positions, quotes, account):
    return []
""")


def test_validate_code_rejects_missing_entrypoint():
    with pytest.raises(CryptoStrategyCodeError, match="decide_crypto"):
        validate_code("""
def decide(price_history):
    return {}
""")


def test_validate_code_rejects_disallowed_import():
    with pytest.raises(CryptoStrategyCodeError, match="os"):
        validate_code("""
import os
def decide_crypto(as_of, positions, quotes, account):
    return []
""")


def test_validate_code_rejects_forbidden_builtin():
    with pytest.raises(CryptoStrategyCodeError, match="eval"):
        validate_code("""
def decide_crypto(as_of, positions, quotes, account):
    eval("1+1")
    return []
""")


def test_validate_code_rejects_dunder_access():
    with pytest.raises(CryptoStrategyCodeError, match="dunder"):
        validate_code("""
def decide_crypto(as_of, positions, quotes, account):
    x = (1).__class__
    return []
""")


def test_run_decide_crypto_returns_cleaned_intents_with_fractional_qty():
    """The one deliberate contract difference from Futures/Forex -- qty is a
    float, since a real BTC position sized to a $1,000 wallet is
    necessarily fractional (see sandbox.py's docstring)."""
    code = """
def decide_crypto(as_of, positions, quotes, account):
    return [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.0025}]
"""
    intents = run_decide_crypto(
        code, as_of="2026-08-23", positions=[], quotes={"BTC/USD": {"price": 77000.0}}, account={"cash": 1000.0},
    )
    assert intents == [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.0025}]


def test_run_decide_crypto_drops_malformed_intents():
    code = """
def decide_crypto(as_of, positions, quotes, account):
    return [
        {"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.001},
        {"action": "bogus", "symbol": "BTC/USD", "side": "buy", "qty": 0.001},
        {"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": -1},
        {"action": "open", "symbol": "", "side": "buy", "qty": 0.001},
        "not a dict",
    ]
"""
    intents = run_decide_crypto(
        code, as_of="2026-08-23", positions=[], quotes={}, account={"cash": 1000.0},
    )
    assert intents == [{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.001}]


def test_run_decide_crypto_swallows_a_raising_strategy():
    code = """
def decide_crypto(as_of, positions, quotes, account):
    raise ValueError("boom")
"""
    intents = run_decide_crypto(
        code, as_of="2026-08-23", positions=[], quotes={}, account={"cash": 1000.0},
    )
    assert intents == []


def test_run_decide_crypto_receives_open_positions():
    code = """
def decide_crypto(as_of, positions, quotes, account):
    if positions:
        return [{"action": "close", "symbol": positions[0]["symbol"], "side": "sell", "qty": positions[0]["qty"]}]
    return []
"""
    intents = run_decide_crypto(
        code, as_of="2026-08-23", positions=[{"symbol": "BTC/USD", "qty": 0.002}], quotes={}, account={"cash": 1000.0},
    )
    assert intents == [{"action": "close", "symbol": "BTC/USD", "side": "sell", "qty": 0.002}]


def test_run_decide_crypto_can_use_next_to_look_up_a_held_qty():
    """Regression test for a real bug caught by live-verifying against real
    Alpaca crypto data: the starter roster's own close-position logic uses
    next(p["qty"] for p in positions if ...) to look up how much of a coin
    is currently held, but the sandbox's restricted builtins were missing
    next() entirely -- every close attempt failed with NameError, silently
    swallowed by run_decide_crypto's own "any failure means no orders this
    cycle" posture, so it looked like the strategy just never triggered
    rather than crashing outright."""
    code = """
def decide_crypto(as_of, positions, quotes, account):
    qty = next(p["qty"] for p in positions if p["symbol"] == "BTC/USD")
    return [{"action": "close", "symbol": "BTC/USD", "side": "sell", "qty": qty}]
"""
    intents = run_decide_crypto(
        code, as_of="2026-08-23", positions=[{"symbol": "BTC/USD", "qty": 0.004}], quotes={}, account={"cash": 1000.0},
    )
    assert intents == [{"action": "close", "symbol": "BTC/USD", "side": "sell", "qty": 0.004}]


def test_run_decide_crypto_cannot_reach_network_or_filesystem():
    code = """
def decide_crypto(as_of, positions, quotes, account):
    import socket
    return []
"""
    with pytest.raises(CryptoStrategyCodeError):
        validate_code(code)
