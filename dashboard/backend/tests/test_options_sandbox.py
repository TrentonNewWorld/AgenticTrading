"""Tests for the options-strategy sandbox (Sub-phase 5 of the Options-
dashboard plan) -- mirrors test_manual10_sandbox.py's coverage exactly, since
domain/options/sandbox.py clones manual10/sandbox.py's security model
verbatim. Only the entrypoint name (decide_options, not decide) and the I/O
shape (order intents, not weights) differ.
"""

from __future__ import annotations

import time

import pytest

from dashboard.backend.domain.options.sandbox import (
    OptionsStrategyCodeError,
    run_decide_options,
    validate_code,
)

VALID_STRATEGY = """
def decide_options(as_of, positions, chain, account):
    intents = []
    for underlying, contracts in chain.items():
        calls = [c for c in contracts if c["right"] == "C"]
        if calls:
            cheapest = min(calls, key=lambda c: c["strike"])
            intents.append({
                "action": "open", "symbol": cheapest["symbol"],
                "side": "buy", "qty": 1, "leg_role": "single",
            })
    return intents
"""

_ACCOUNT = {"cash": 10000.0, "equity": 10000.0}
_CHAIN = {
    "AAPL": [
        {"symbol": "AAPL260918C00180000", "strike": 180.0, "expiration": "2026-09-18",
         "right": "C", "bid": 5.0, "ask": 5.2, "last": 5.1, "open_interest": 100},
        {"symbol": "AAPL260918C00190000", "strike": 190.0, "expiration": "2026-09-18",
         "right": "C", "bid": 1.0, "ask": 1.2, "last": 1.1, "open_interest": 50},
    ],
}


def test_valid_strategy_passes_validation():
    validate_code(VALID_STRATEGY)  # must not raise


def test_missing_decide_options_function_is_rejected():
    with pytest.raises(OptionsStrategyCodeError, match="must define"):
        validate_code("def wrong_name(a, b, c, d):\n    return []")


def test_old_style_decide_function_is_also_rejected():
    """The equities decide(price_history) contract must NOT satisfy the
    options entrypoint check -- the two are deliberately incompatible
    contracts, per the decision that options strategies are full
    contract-level, not a weights-based overlay."""
    with pytest.raises(OptionsStrategyCodeError, match="must define"):
        validate_code("def decide(price_history):\n    return {}")


def test_syntax_error_is_rejected():
    with pytest.raises(OptionsStrategyCodeError, match="syntax error"):
        validate_code("def decide_options(a, b, c, d)\n    return []")


@pytest.mark.parametrize("bad_code,expected_fragment", [
    ("import os\ndef decide_options(a,b,c,d):\n    return []", "import of 'os'"),
    ("import subprocess\ndef decide_options(a,b,c,d):\n    return []", "import of 'subprocess'"),
    ("import socket\ndef decide_options(a,b,c,d):\n    return []", "import of 'socket'"),
    ("from os import system\ndef decide_options(a,b,c,d):\n    return []", "import of 'os'"),
    ("def decide_options(a,b,c,d):\n    eval('1')\n    return []", "use of 'eval'"),
    ("def decide_options(a,b,c,d):\n    exec('pass')\n    return []", "use of 'exec'"),
    ("def decide_options(a,b,c,d):\n    __import__('os')\n    return []", "use of '__import__'"),
    ("def decide_options(a,b,c,d):\n    open('/etc/passwd')\n    return []", "use of 'open'"),
    ("def decide_options(a,b,c,d):\n    return a.__class__.__bases__", "dunder attribute"),
    ("def decide_options(a,b,c,d):\n    getattr(a, 'foo')\n    return []", "use of 'getattr'"),
])
def test_dangerous_patterns_are_rejected(bad_code, expected_fragment):
    with pytest.raises(OptionsStrategyCodeError, match=expected_fragment):
        validate_code(bad_code)


def test_allowed_imports_pass():
    code = (
        "import math\nimport statistics\nimport json\nimport datetime\n"
        "def decide_options(a,b,c,d):\n    return []"
    )
    validate_code(code)  # must not raise


def test_run_decide_options_executes_a_valid_strategy():
    intents = run_decide_options(
        VALID_STRATEGY, as_of="2026-08-21", positions=[], chain=_CHAIN, account=_ACCOUNT,
    )
    assert intents == [{
        "action": "open", "symbol": "AAPL260918C00180000",
        "side": "buy", "qty": 1, "leg_role": "single",
    }]


def test_run_decide_options_can_return_multi_leg_intents():
    """A covered-call-shaped strategy returning two intents (buy stock +
    sell call) -- the sandbox itself does no pairing/grouping, it only
    validates and passes each intent through; grouping is the engine's job."""
    code = """
def decide_options(as_of, positions, chain, account):
    return [
        {"action": "open", "symbol": "AAPL", "side": "buy", "qty": 100, "leg_role": "stock"},
        {"action": "open", "symbol": "AAPL260918C00190000", "side": "sell", "qty": 1, "leg_role": "option"},
    ]
"""
    intents = run_decide_options(code, as_of="2026-08-21", positions=[], chain=_CHAIN, account=_ACCOUNT)
    assert len(intents) == 2
    assert intents[0]["leg_role"] == "stock"
    assert intents[1]["leg_role"] == "option"


def test_run_decide_options_times_out_gracefully_on_an_infinite_loop():
    code = "def decide_options(a,b,c,d):\n    while True:\n        pass\n"
    start = time.time()
    result = run_decide_options(code, as_of="2026-08-21", positions=[], chain={}, account=_ACCOUNT, timeout=2)
    elapsed = time.time() - start
    assert result == []
    assert elapsed < 5  # bounded by the timeout, not left hanging


def test_run_decide_options_treats_a_runtime_exception_as_no_orders():
    code = "def decide_options(a,b,c,d):\n    return 1 / 0\n"
    assert run_decide_options(code, as_of="2026-08-21", positions=[], chain={}, account=_ACCOUNT) == []


def test_run_decide_options_treats_non_list_return_as_no_orders():
    code = "def decide_options(a,b,c,d):\n    return 'not a list'\n"
    assert run_decide_options(code, as_of="2026-08-21", positions=[], chain={}, account=_ACCOUNT) == []


@pytest.mark.parametrize("bad_intent", [
    {"action": "bogus", "symbol": "AAPL260918C00180000", "side": "buy", "qty": 1},
    {"action": "open", "symbol": "AAPL260918C00180000", "side": "bogus", "qty": 1},
    {"action": "open", "symbol": "", "side": "buy", "qty": 1},
    {"action": "open", "symbol": "AAPL260918C00180000", "side": "buy", "qty": 0},
    {"action": "open", "symbol": "AAPL260918C00180000", "side": "buy", "qty": -1},
    {"action": "open", "symbol": "AAPL260918C00180000", "side": "buy", "qty": "not-a-number"},
    {"symbol": "AAPL260918C00180000", "side": "buy", "qty": 1},  # missing action
])
def test_run_decide_options_drops_malformed_intents(bad_intent):
    code = f"def decide_options(a,b,c,d):\n    return [{bad_intent!r}]\n"
    assert run_decide_options(code, as_of="2026-08-21", positions=[], chain={}, account=_ACCOUNT) == []


def test_run_decide_options_defaults_leg_role_to_single():
    code = (
        'def decide_options(a,b,c,d):\n'
        '    return [{"action": "open", "symbol": "AAPL260918C00180000", "side": "buy", "qty": 1}]\n'
    )
    intents = run_decide_options(code, as_of="2026-08-21", positions=[], chain={}, account=_ACCOUNT)
    assert intents == [{
        "action": "open", "symbol": "AAPL260918C00180000",
        "side": "buy", "qty": 1, "leg_role": "single",
    }]


def test_run_decide_options_has_no_access_to_environment_secrets(monkeypatch):
    """Even if a strategy somehow got an env-reading primitive, the child
    process's environment must not contain real credentials -- pins that
    run_decide_options always passes env={}, mirroring manual10's own
    equivalent test."""
    monkeypatch.setenv("ALPACA_API_KEY", "super-secret-value-should-never-leak")
    code = "import os\ndef decide_options(a,b,c,d):\n    return []\n"
    with pytest.raises(OptionsStrategyCodeError):
        validate_code(code)
    import inspect

    import dashboard.backend.domain.options.sandbox as sandbox_module
    source = inspect.getsource(sandbox_module.run_decide_options)
    assert "env={}" in source
