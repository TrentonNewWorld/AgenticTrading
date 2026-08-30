from __future__ import annotations

import pytest

from dashboard.backend.domain.futures.sandbox import (
    FuturesStrategyCodeError,
    run_decide_futures,
    validate_code,
)


def test_validate_code_accepts_a_well_formed_strategy():
    validate_code("""
def decide_futures(as_of, positions, quotes, account):
    return []
""")


def test_validate_code_rejects_missing_entrypoint():
    with pytest.raises(FuturesStrategyCodeError, match="decide_futures"):
        validate_code("""
def decide(price_history):
    return {}
""")


def test_validate_code_rejects_disallowed_import():
    with pytest.raises(FuturesStrategyCodeError, match="os"):
        validate_code("""
import os
def decide_futures(as_of, positions, quotes, account):
    return []
""")


def test_validate_code_rejects_forbidden_builtin():
    with pytest.raises(FuturesStrategyCodeError, match="eval"):
        validate_code("""
def decide_futures(as_of, positions, quotes, account):
    eval("1+1")
    return []
""")


def test_validate_code_rejects_dunder_access():
    with pytest.raises(FuturesStrategyCodeError, match="dunder"):
        validate_code("""
def decide_futures(as_of, positions, quotes, account):
    x = (1).__class__
    return []
""")


def test_run_decide_futures_returns_cleaned_intents():
    code = """
def decide_futures(as_of, positions, quotes, account):
    return [{"action": "open", "symbol": "ES=F", "side": "buy", "qty": 1}]
"""
    intents = run_decide_futures(
        code, as_of="2026-08-23", positions=[], quotes={"ES=F": {"price": 5000.0}}, account={"cash": 1000.0},
    )
    assert intents == [{"action": "open", "symbol": "ES=F", "side": "buy", "qty": 1}]


def test_run_decide_futures_drops_malformed_intents():
    code = """
def decide_futures(as_of, positions, quotes, account):
    return [
        {"action": "open", "symbol": "ES=F", "side": "buy", "qty": 1},
        {"action": "bogus", "symbol": "ES=F", "side": "buy", "qty": 1},
        {"action": "open", "symbol": "ES=F", "side": "buy", "qty": -1},
        {"action": "open", "symbol": "", "side": "buy", "qty": 1},
        "not a dict",
    ]
"""
    intents = run_decide_futures(
        code, as_of="2026-08-23", positions=[], quotes={}, account={"cash": 1000.0},
    )
    assert intents == [{"action": "open", "symbol": "ES=F", "side": "buy", "qty": 1}]


def test_run_decide_futures_swallows_a_raising_strategy():
    code = """
def decide_futures(as_of, positions, quotes, account):
    raise ValueError("boom")
"""
    intents = run_decide_futures(
        code, as_of="2026-08-23", positions=[], quotes={}, account={"cash": 1000.0},
    )
    assert intents == []


def test_run_decide_futures_receives_open_positions():
    code = """
def decide_futures(as_of, positions, quotes, account):
    if positions:
        return [{"action": "close", "symbol": positions[0]["symbol"], "side": "sell", "qty": positions[0]["qty"]}]
    return []
"""
    intents = run_decide_futures(
        code, as_of="2026-08-23", positions=[{"symbol": "ES=F", "qty": 2}], quotes={}, account={"cash": 1000.0},
    )
    assert intents == [{"action": "close", "symbol": "ES=F", "side": "sell", "qty": 2}]


def test_run_decide_futures_cannot_reach_network_or_filesystem():
    code = """
def decide_futures(as_of, positions, quotes, account):
    import socket
    return []
"""
    with pytest.raises(FuturesStrategyCodeError):
        validate_code(code)
