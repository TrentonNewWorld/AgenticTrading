"""Unit tests for ``ATLClient`` using a fake HTTP transport (no network)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import newworldtrading.legacy_client as legacy_client
from newworldtrading import (
    ATLAPIError,
    ATLAuthenticationError,
    ATLClient,
    ATLConflictError,
    ATLRunFailedError,
    ATLTimeoutError,
    ATLValidationError,
    Decision,
    ExecutionResult,
    Order,
    RunResult,
    Step,
)

API_KEY = "ag_secret_should_never_leak"


def _client() -> ATLClient:
    return ATLClient("http://test.local", API_KEY, timeout=5)


def _exec_payload(decision_id="dec_1", run_status="running"):
    return {
        "protocol_version": "1.0",
        "run_id": "run_1",
        "step_id": "step_1",
        "decision_id": decision_id,
        "accepted": True,
        "validation": {"passed": True, "warnings": [], "rejections": []},
        "fills": [],
        "portfolio_after": {"cash": 100, "equity": 100, "positions": []},
        "run_status": run_status,
    }


# -- request construction / auth ---------------------------------------


def test_create_run_request_construction(fake_http):
    captured = {}

    def responder(req):
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        captured["api_key"] = req.get_header("X-api-key")
        captured["body"] = json.loads(req.data.decode())
        return (200, {"run_id": "run_1", "status": "created",
                      "environment": {"environment_id": "us-equity-hourly-v1", "type": "backtest"},
                      "config": {"symbols": ["AAPL"]}})

    fake_http(responder)
    run = _client().create_run(
        "agv_1",
        environment_id="us-equity-hourly-v1",
        start_date="2026-04-15",
        end_date="2026-04-16",
        symbols=["AAPL"],
    )
    assert captured["method"] == "POST"
    assert captured["url"] == "http://test.local/api/v1/runs"
    assert captured["api_key"] == API_KEY
    assert captured["body"]["agent_version_id"] == "agv_1"
    assert captured["body"]["environment"] == {"type": "backtest", "environment_id": "us-equity-hourly-v1"}
    assert captured["body"]["config"]["symbols"] == ["AAPL"]
    # initial_cash is NOT advertised on the wire — the environment fixes capital.
    assert "initial_cash" not in captured["body"]["config"]
    assert run.id == "run_1"
    assert run.run_id == "run_1"
    assert run.environment_id == "us-equity-hourly-v1"


def test_create_run_requires_agent_version():
    with pytest.raises(ATLValidationError):
        _client().create_run("", environment_id="e", start_date="a", end_date="b")


def test_create_run_rejects_custom_initial_cash():
    """MEDIUM #12 — the environment fixes starting capital, so a non-default
    initial_cash is rejected client-side (fail fast) with a clear code, before
    any HTTP request the backend would 400."""
    with pytest.raises(ATLValidationError) as exc:
        _client().create_run(
            "agv_1", environment_id="e", start_date="a", end_date="b",
            initial_cash=50_000,
        )
    assert exc.value.code == "initial_cash_fixed"


def test_create_run_rejects_custom_initial_cash_via_config():
    """The guard can't be bypassed by smuggling initial_cash through the raw
    config dict (config is merged into run_config, so it must be re-checked)."""
    with pytest.raises(ATLValidationError) as exc:
        _client().create_run(
            "agv_1", environment_id="e", start_date="a", end_date="b",
            config={"initial_cash": 50_000},
        )
    assert exc.value.code == "initial_cash_fixed"


def test_create_run_accepts_the_fixed_default_initial_cash(fake_http):
    """Passing the fixed default is tolerated (backward compat) and still omitted
    from the wire payload."""
    captured = {}

    def responder(req):
        captured["body"] = json.loads(req.data.decode())
        return (200, {"run_id": "run_1", "status": "created",
                      "environment": {"environment_id": "e", "type": "backtest"},
                      "config": {}})

    fake_http(responder)
    _client().create_run(
        "agv_1", environment_id="e", start_date="a", end_date="b",
        initial_cash=1_000,
    )
    assert "initial_cash" not in captured["body"]["config"]


def test_api_key_not_in_repr():
    assert API_KEY not in repr(_client())


# -- typed parsing -----------------------------------------------------


def test_get_next_step_typed_parsing(fake_http):
    payload = {
        "protocol_version": "1.0",
        "run_id": "run_1",
        "step_id": "step_1",
        "sequence": 0,
        "timestamp": "2026-04-15T14:00:00Z",
        "deadline_at": "2026-04-15T14:01:00Z",
        "status": "awaiting_decision",
        "observation": {
            "market": {"bars": {}, "features": {"AAPL": {"price": 210.0}}, "events": []},
            "portfolio": {"cash": 1_000, "equity": 1_000, "positions": []},
        },
        "constraints": {"allowed_symbols": ["AAPL"], "allow_short": False, "max_orders": 5},
    }
    fake_http(lambda req: (200, payload))
    step = _client().get_next_step("run_1")
    assert isinstance(step, Step)
    assert step.status == "awaiting_decision"
    assert step.id == "step_1" == step.step_id
    assert step.sequence == 0
    assert step.observation.features == {"AAPL": {"price": 210.0}}
    assert step.observation.portfolio["cash"] == 1_000
    assert step.constraints["max_orders"] == 5
    assert step.raw == payload  # .raw preserved for forward compat


def test_get_run_result_metrics(fake_http):
    payload = {
        "run_id": "run_1",
        "result_run_id": "ext_1",
        "status": "completed",
        "metrics": {"total_return": 0.123, "sharpe_ratio": 1.4, "num_trades": 5},
        "trades": [{"symbol": "AAPL"}],
        "equity_curve": [],
        "decisions": [],
        "run": {"run_id": "ext_1"},
    }
    fake_http(lambda req: (200, payload))
    result = _client().get_run_result("run_1")
    assert isinstance(result, RunResult)
    assert result.metrics["total_return"] == 0.123
    assert result.trades == [{"symbol": "AAPL"}]


def test_list_environments(fake_http):
    fake_http(lambda req: (200, {"environments": [{"environment_id": "us-equity-hourly-v1"}]}))
    envs = _client().list_environments()
    assert envs == [{"environment_id": "us-equity-hourly-v1"}]


def test_get_run_trades_unwraps_list(fake_http):
    fake_http(lambda req: (200, {"trades": [{"symbol": "AAPL"}], "count": 1}))
    assert _client().get_run_trades("run_1") == [{"symbol": "AAPL"}]


# -- decisions ---------------------------------------------------------


def test_submit_hold_sends_empty_orders(fake_http):
    captured = {}

    def responder(req):
        captured["body"] = json.loads(req.data.decode())
        return (200, _exec_payload())

    fake_http(responder)
    res = _client().submit_decision("run_1", "step_1", Decision(orders=[], rationale="No valid signal."))
    assert isinstance(res, ExecutionResult)
    assert captured["body"]["orders"] == []
    assert captured["body"]["rationale"] == "No valid signal."
    assert captured["body"]["idempotency_key"] == "run_1:step_1"


def test_submit_valid_order(fake_http):
    captured = {}

    def responder(req):
        captured["body"] = json.loads(req.data.decode())
        return (200, _exec_payload())

    fake_http(responder)
    decision = Decision(
        orders=[Order(symbol="AAPL", side="buy", quantity_type="shares", quantity=10, order_type="market")],
        confidence=0.8,
        rationale="Positive momentum.",
    )
    _client().submit_decision("run_1", "step_1", decision)
    assert captured["body"]["orders"][0] == {
        "symbol": "AAPL", "side": "buy", "quantity_type": "shares",
        "quantity": 10, "order_type": "market",
    }
    assert captured["body"]["confidence"] == 0.8


def test_submit_dict_decision_with_order_objects(fake_http):
    captured = {}

    def responder(req):
        captured["body"] = json.loads(req.data.decode())
        return (200, _exec_payload())

    fake_http(responder)
    _client().submit_decision(
        "run_1", "step_1",
        {"orders": [Order("MSFT", "buy", 5)], "rationale": "rule"},
    )
    assert captured["body"]["orders"][0]["symbol"] == "MSFT"
    assert captured["body"]["idempotency_key"] == "run_1:step_1"


def test_explicit_idempotency_key(fake_http):
    captured = {}

    def responder(req):
        captured["body"] = json.loads(req.data.decode())
        return (200, _exec_payload())

    fake_http(responder)
    _client().submit_decision("run_1", "step_1", Decision(orders=[]), idempotency_key="custom-key")
    assert captured["body"]["idempotency_key"] == "custom-key"


def test_retry_reuses_idempotency_key_and_returns_original(fake_http):
    """Server accepts, client 'loses' the response, then retries the same step."""

    class IdempotentBackend:
        def __init__(self):
            self.executions = 0
            self.store = {}

        def __call__(self, req):
            body = json.loads(req.data.decode())
            key = body["idempotency_key"]
            if key in self.store:
                return (200, self.store[key])  # replay, no new execution
            self.executions += 1
            result = _exec_payload(decision_id=f"dec_{self.executions}")
            self.store[key] = result
            return (200, result)

    backend = IdempotentBackend()
    fh = fake_http(backend)
    client = _client()
    decision = Decision(orders=[Order("AAPL", "buy", 10)])

    first = client.submit_decision("run_1", "step_1", decision)
    second = client.submit_decision("run_1", "step_1", decision)  # retry after lost response

    assert backend.executions == 1  # executed exactly once
    assert first.decision_id == second.decision_id == "dec_1"
    sent_keys = [json.loads(r.data.decode())["idempotency_key"] for r in fh.requests]
    assert sent_keys == ["run_1:step_1", "run_1:step_1"]


# -- error mapping -----------------------------------------------------


def test_validation_error_422_list_detail(fake_http):
    fake_http(lambda req: (422, {"detail": [{"msg": "field required", "loc": ["body", "x"]}]}))
    with pytest.raises(ATLValidationError) as ei:
        _client().get_run("run_1")
    assert ei.value.status_code == 422
    assert "field required" in ei.value.message


def test_validation_error_protocol_envelope(fake_http):
    fake_http(lambda req: (400, {"detail": {"protocol_version": "1.0",
                                            "error": {"code": "invalid_symbols", "message": "bad symbols"}}}))
    with pytest.raises(ATLValidationError) as ei:
        _client().get_run("run_1")
    assert ei.value.code == "invalid_symbols"
    assert ei.value.message == "bad symbols"


def test_authentication_error_mapping(fake_http):
    fake_http(lambda req: (401, {"detail": "Invalid API key"}))
    with pytest.raises(ATLAuthenticationError) as ei:
        _client().get_run("run_1")
    assert ei.value.status_code == 401
    assert ei.value.message == "Invalid API key"


def test_conflict_error_mapping(fake_http):
    fake_http(lambda req: (409, {"detail": {"error": {"code": "step_already_finalized",
                                                      "message": "already finalized"}}}))
    with pytest.raises(ATLConflictError) as ei:
        _client().submit_decision("run_1", "step_1", Decision(orders=[]))
    assert ei.value.code == "step_already_finalized"


def test_run_failed_mapping(fake_http):
    fake_http(lambda req: (500, {"detail": {"error": {"code": "run_failed", "message": "boom"}}}))
    with pytest.raises(ATLRunFailedError) as ei:
        _client().get_next_step("run_1")
    assert ei.value.message == "boom"


def test_generic_api_error(fake_http):
    fake_http(lambda req: (503, {"detail": "service unavailable"}))
    with pytest.raises(ATLAPIError) as ei:
        _client().get_run("run_1")
    assert ei.value.status_code == 503
    assert type(ei.value) is ATLAPIError


# -- transport edge cases ----------------------------------------------


def test_empty_response_returns_none(monkeypatch):
    class _Empty:
        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(legacy_client.urllib.request, "urlopen", lambda req, timeout=None: _Empty())
    assert _client()._request("GET", "/x") is None


def test_non_json_error_body(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "err", {}, io.BytesIO(b"<html>nope</html>"))

    monkeypatch.setattr(legacy_client.urllib.request, "urlopen", boom)
    with pytest.raises(ATLAPIError) as ei:
        _client().get_run("run_1")
    assert ei.value.status_code == 500
    assert "nope" in ei.value.message


def test_timeout_mapping(monkeypatch):
    def boom(req, timeout=None):
        raise TimeoutError("slow")

    monkeypatch.setattr(legacy_client.urllib.request, "urlopen", boom)
    with pytest.raises(ATLTimeoutError):
        _client().get_run("run_1")


def test_socket_timeout_maps_to_timeout_error(monkeypatch):
    """A mid-read socket timeout (the type ``resp.read()`` actually raises)
    must surface as ATLTimeoutError, exactly like a connect timeout."""
    import socket

    def boom(req, timeout=None):
        raise socket.timeout("The read operation timed out")

    monkeypatch.setattr(legacy_client.urllib.request, "urlopen", boom)
    with pytest.raises(ATLTimeoutError):
        _client().get_run("run_1")


def test_client_catches_socket_timeout_explicitly():
    """LOW #13 — on Python 3.9 (the package's floor), ``socket.timeout`` is
    NOT an alias of TimeoutError (they merged in 3.10), so a bare
    ``except TimeoutError`` lets a mid-read timeout escape as a raw
    ``socket.timeout``. Source-level guard because on CI's ≥3.10 the two are
    runtime-indistinguishable."""
    import inspect

    src = inspect.getsource(legacy_client)
    assert "except socket.timeout" in src, (
        "legacy_client must catch socket.timeout explicitly for Python 3.9 support"
    )
