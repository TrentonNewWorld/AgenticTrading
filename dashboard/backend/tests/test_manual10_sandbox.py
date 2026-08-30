"""Tests for the uploaded-strategy sandbox -- the actual security boundary
for arbitrary uploaded code (see sandbox.py's module docstring for the three
layers). Every known Python sandbox-escape pattern here must be rejected by
validate_code() before it's ever executed; run_decide() tests confirm the
subprocess isolation and timeout actually hold under real execution.
"""

from __future__ import annotations

import time

import pytest

from dashboard.backend.domain.manual10.sandbox import StrategyCodeError, run_decide, validate_code

VALID_STRATEGY = """
def decide(price_history):
    weights = {}
    for symbol, points in price_history.items():
        if len(points) >= 2 and points[-1]["close"] > points[0]["close"]:
            weights[symbol] = 1.0
    return weights
"""


def test_valid_strategy_passes_validation():
    validate_code(VALID_STRATEGY)  # must not raise


def test_missing_decide_function_is_rejected():
    with pytest.raises(StrategyCodeError, match="must define"):
        validate_code("def wrong_name(x):\n    return {}")


def test_syntax_error_is_rejected():
    with pytest.raises(StrategyCodeError, match="syntax error"):
        validate_code("def decide(x)\n    return {}")


@pytest.mark.parametrize("bad_code,expected_fragment", [
    ("import os\ndef decide(x):\n    return {}", "import of 'os'"),
    ("import subprocess\ndef decide(x):\n    return {}", "import of 'subprocess'"),
    ("import socket\ndef decide(x):\n    return {}", "import of 'socket'"),
    ("from os import system\ndef decide(x):\n    return {}", "import of 'os'"),
    ("def decide(x):\n    eval('1')\n    return {}", "use of 'eval'"),
    ("def decide(x):\n    exec('pass')\n    return {}", "use of 'exec'"),
    ("def decide(x):\n    __import__('os')\n    return {}", "use of '__import__'"),
    ("def decide(x):\n    open('/etc/passwd')\n    return {}", "use of 'open'"),
    ("def decide(x):\n    return ().__class__.__bases__[0].__subclasses__()", "dunder attribute"),
    ("def decide(x):\n    return x.__class__.__dict__", "dunder attribute"),
    ("def decide(x):\n    getattr(x, 'foo')\n    return {}", "use of 'getattr'"),
])
def test_dangerous_patterns_are_rejected(bad_code, expected_fragment):
    with pytest.raises(StrategyCodeError, match=expected_fragment):
        validate_code(bad_code)


def test_allowed_imports_pass():
    code = "import math\nimport statistics\nimport json\nimport datetime\ndef decide(x):\n    return {}"
    validate_code(code)  # must not raise


def test_run_decide_executes_a_valid_strategy():
    history = {
        "AAPL": [{"t": "2026-01-01", "close": 100}, {"t": "2026-01-02", "close": 110}],
        "MSFT": [{"t": "2026-01-01", "close": 200}, {"t": "2026-01-02", "close": 190}],
    }
    weights = run_decide(VALID_STRATEGY, history)
    assert weights == {"AAPL": 1.0}


def test_run_decide_times_out_gracefully_on_an_infinite_loop():
    code = "def decide(x):\n    while True:\n        pass\n"
    start = time.time()
    result = run_decide(code, {}, timeout=2)
    elapsed = time.time() - start
    assert result == {}
    assert elapsed < 5  # bounded by the timeout, not left hanging


def test_run_decide_treats_a_runtime_exception_as_no_positions():
    code = "def decide(x):\n    return 1 / 0\n"
    assert run_decide(code, {}) == {}


def test_run_decide_treats_non_dict_return_as_no_positions():
    code = "def decide(x):\n    return 'not a dict'\n"
    assert run_decide(code, {}) == {}


def test_run_decide_drops_non_numeric_and_non_positive_weights():
    code = "def decide(x):\n    return {'AAPL': 1.0, 'BAD': 'oops', 'ZERO': 0, 'NEG': -1}\n"
    assert run_decide(code, {}) == {"AAPL": 1.0}


def test_run_decide_has_no_access_to_environment_secrets(monkeypatch):
    """Even if a strategy somehow got an env-reading primitive, the child
    process's environment must not contain real credentials -- this pins
    that run_decide always passes env={}, not the parent's os.environ."""
    monkeypatch.setenv("ALPACA_API_KEY", "super-secret-value-should-never-leak")
    code = (
        "import os\n"  # this alone gets rejected by validate_code, but run_decide
        "def decide(x):\n"
        "    return {}\n"
    )
    with pytest.raises(StrategyCodeError):
        validate_code(code)
    # Even bypassing validate_code (simulating a hypothetical AST-check gap),
    # confirm the subprocess truly has an empty environment via a code path
    # validate_code doesn't block: printing os.environ is impossible without
    # `import os`, which is already rejected -- so the real assertion is that
    # run_decide is always called with env={}, verified structurally here.
    import dashboard.backend.domain.manual10.sandbox as sandbox_module
    import inspect
    source = inspect.getsource(sandbox_module.run_decide)
    assert "env={}" in source
