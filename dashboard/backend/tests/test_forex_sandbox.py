from __future__ import annotations

import pytest

from dashboard.backend.domain.forex.sandbox import (
    ForexStrategyCodeError,
    run_decide_forex,
    validate_code,
)


def test_validate_code_accepts_a_well_formed_strategy():
    validate_code("""
def decide_forex(as_of, positions, quotes, account):
    return []
""")


def test_validate_code_rejects_missing_entrypoint():
    with pytest.raises(ForexStrategyCodeError, match="decide_forex"):
        validate_code("""
def decide(price_history):
    return {}
""")


def test_validate_code_rejects_disallowed_import():
    with pytest.raises(ForexStrategyCodeError, match="os"):
        validate_code("""
import os
def decide_forex(as_of, positions, quotes, account):
    return []
""")


def test_validate_code_rejects_forbidden_builtin():
    with pytest.raises(ForexStrategyCodeError, match="eval"):
        validate_code("""
def decide_forex(as_of, positions, quotes, account):
    eval("1+1")
    return []
""")


def test_validate_code_rejects_dunder_access():
    with pytest.raises(ForexStrategyCodeError, match="dunder"):
        validate_code("""
def decide_forex(as_of, positions, quotes, account):
    x = (1).__class__
    return []
""")


def test_run_decide_forex_returns_cleaned_intents():
    code = """
def decide_forex(as_of, positions, quotes, account):
    return [{"action": "open", "symbol": "EURUSD=X", "side": "buy", "qty": 500}]
"""
    intents = run_decide_forex(
        code, as_of="2026-08-23", positions=[], quotes={"EURUSD=X": {"price": 1.08}}, account={"cash": 1000.0},
    )
    assert intents == [{"action": "open", "symbol": "EURUSD=X", "side": "buy", "qty": 500}]


def test_run_decide_forex_drops_malformed_intents():
    code = """
def decide_forex(as_of, positions, quotes, account):
    return [
        {"action": "open", "symbol": "EURUSD=X", "side": "buy", "qty": 500},
        {"action": "bogus", "symbol": "EURUSD=X", "side": "buy", "qty": 500},
        {"action": "open", "symbol": "EURUSD=X", "side": "buy", "qty": -1},
        {"action": "open", "symbol": "", "side": "buy", "qty": 500},
        "not a dict",
    ]
"""
    intents = run_decide_forex(
        code, as_of="2026-08-23", positions=[], quotes={}, account={"cash": 1000.0},
    )
    assert intents == [{"action": "open", "symbol": "EURUSD=X", "side": "buy", "qty": 500}]


def test_run_decide_forex_swallows_a_raising_strategy():
    code = """
def decide_forex(as_of, positions, quotes, account):
    raise ValueError("boom")
"""
    intents = run_decide_forex(
        code, as_of="2026-08-23", positions=[], quotes={}, account={"cash": 1000.0},
    )
    assert intents == []


def test_run_decide_forex_receives_open_positions():
    code = """
def decide_forex(as_of, positions, quotes, account):
    if positions:
        return [{"action": "close", "symbol": positions[0]["symbol"], "side": "sell", "qty": positions[0]["qty"]}]
    return []
"""
    intents = run_decide_forex(
        code, as_of="2026-08-23", positions=[{"symbol": "EURUSD=X", "qty": 500}], quotes={}, account={"cash": 1000.0},
    )
    assert intents == [{"action": "close", "symbol": "EURUSD=X", "side": "sell", "qty": 500}]


def test_run_decide_forex_cannot_reach_network_or_filesystem():
    code = """
def decide_forex(as_of, positions, quotes, account):
    import socket
    return []
"""
    with pytest.raises(ForexStrategyCodeError):
        validate_code(code)
