"""``ATLClient`` - a remote HTTP client for the Agent-Environment Protocol.

The client is a thin, typed wrapper over the existing REST endpoints
(``/api/v1/...``). It uses only the Python standard library so the package
stays dependency-free, and it authenticates with the agent API key via the
``X-API-Key`` header. It never logs or prints the API key.

Conceptual mapping::

    Python method            REST endpoint
    ---------------------    --------------------------------------------
    create_agent_version  -> POST /api/v1/agents/{agent_id}/versions
    create_run            -> POST /api/v1/runs
    get_run               -> GET  /api/v1/runs/{run_id}
    get_run_status        -> GET  /api/v1/runs/{run_id}/status
    get_next_step         -> GET  /api/v1/runs/{run_id}/steps/next
    get_step              -> GET  /api/v1/runs/{run_id}/steps/{step_id}
    submit_decision       -> POST /api/v1/runs/{run_id}/steps/{step_id}/decision
    get_run_result        -> GET  /api/v1/runs/{run_id}/result
    get_run_trades        -> GET  /api/v1/runs/{run_id}/trades
    get_run_decisions     -> GET  /api/v1/runs/{run_id}/decisions
    get_run_metrics       -> GET  /api/v1/runs/{run_id}/metrics
    list_environments     -> GET  /api/v1/environments
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Union

from .exceptions import (
    ATLAPIError,
    ATLAuthenticationError,
    ATLConflictError,
    ATLRunFailedError,
    ATLTimeoutError,
    ATLValidationError,
)
from .models import (
    AgentVersion,
    Decision,
    ExecutionResult,
    Order,
    Run,
    RunResult,
    RunStatus,
    Step,
)


class ATLClient:
    """Remote client for the NewWorldTrading Agent-Environment API.

    Parameters
    ----------
    base_url:
        Base URL of the API server, e.g. ``http://127.0.0.1:8000``.
    api_key:
        Agent API key (``ag_...``), sent as the ``X-API-Key`` header.
    timeout:
        Default per-request timeout in seconds.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not api_key:
            raise ValueError("api_key is required")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout

    # -- transport ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"

        headers = {"Accept": "application/json", "X-API-Key": self._api_key}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            self._raise_for_error(exc, path)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or isinstance(reason, OSError) and "timed out" in str(reason):
                raise ATLTimeoutError(f"request timed out: {reason}", path=path) from exc
            raise ATLAPIError(f"connection error: {reason}", path=path) from exc
        except socket.timeout as exc:
            # socket.timeout, not TimeoutError: on Python 3.9 (our floor) they
            # are distinct types and a mid-read timeout raises socket.timeout.
            # From 3.10 on socket.timeout IS TimeoutError, so this clause is
            # equivalent there.
            raise ATLTimeoutError(f"request timed out after {timeout or self.timeout}s", path=path) from exc

    def _raise_for_error(self, exc: "urllib.error.HTTPError", path: str) -> None:
        text = ""
        try:
            text = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            pass
        parsed: Any = None
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = text

        detail: Any = parsed
        if isinstance(parsed, dict) and "detail" in parsed:
            detail = parsed["detail"]

        code: Optional[str] = None
        message: Optional[str] = None
        if isinstance(detail, dict):
            err = detail.get("error")
            if isinstance(err, dict):
                code = err.get("code")
                message = err.get("message")
            else:
                message = detail.get("message")
                if message is None:
                    message = json.dumps(detail)
        elif isinstance(detail, list):  # FastAPI/pydantic 422 shape
            try:
                message = "; ".join(str(e.get("msg", e)) for e in detail)
            except Exception:  # pragma: no cover - defensive
                message = str(detail)
        elif detail is not None:
            message = str(detail)

        status = exc.code
        message = message or f"HTTP {status} error"
        kwargs = dict(status_code=status, path=path, code=code, response=parsed)

        if code == "run_failed":
            raise ATLRunFailedError(message, **kwargs)
        if status in (401, 403):
            raise ATLAuthenticationError(message, **kwargs)
        if status in (400, 422):
            raise ATLValidationError(message, **kwargs)
        if status == 409:
            raise ATLConflictError(message, **kwargs)
        raise ATLAPIError(message, **kwargs)

    # -- agent versions ----------------------------------------------------

    def create_agent_version(
        self,
        agent_id: str,
        *,
        version: str,
        model_backbones: Optional[List[str]] = None,
        architecture: Optional[str] = None,
        decision_frequency: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentVersion:
        """Create an immutable AgentVersion (create once, reuse across runs)."""
        body: Dict[str, Any] = {"version": version}
        if model_backbones is not None:
            body["model_backbones"] = model_backbones
        if architecture is not None:
            body["architecture"] = architecture
        if decision_frequency is not None:
            body["decision_frequency"] = decision_frequency
        if metadata:
            allowed = (
                "execution_mode",
                "code_commit",
                "verification_level",
                "prompt",
                "config",
                "prompt_hash",
                "config_hash",
            )
            for key in allowed:
                if key in metadata:
                    body[key] = metadata[key]
        resp = self._request("POST", f"/api/v1/agents/{agent_id}/versions", body=body)
        return AgentVersion.from_dict(resp.get("agent_version", resp))

    def list_agent_versions(self, agent_id: str) -> List[AgentVersion]:
        resp = self._request("GET", f"/api/v1/agents/{agent_id}/versions")
        return [AgentVersion.from_dict(v) for v in (resp.get("versions") or [])]

    def get_agent_version(self, agent_version_id: str) -> AgentVersion:
        resp = self._request("GET", f"/api/v1/agent-versions/{agent_version_id}")
        return AgentVersion.from_dict(resp.get("agent_version", resp))

    # -- runs --------------------------------------------------------------

    def create_run(
        self,
        agent_version_id: str,
        *,
        environment_id: str,
        start_date: str,
        end_date: str,
        symbols: Optional[List[str]] = None,
        initial_cash: Optional[float] = None,
        environment_type: str = "backtest",
        config: Optional[Dict[str, Any]] = None,
    ) -> Run:
        """Create a run for an existing AgentVersion. Requires ``agent_version_id``.

        ``initial_cash`` is accepted for forward-compatibility but the backtest
        environment currently fixes starting capital, so a value other than the
        fixed default is rejected client-side (fail fast) rather than making a
        request the backend would reject with ``invalid_config``. Leave it unset —
        the environment applies the fixed default.
        """
        if not agent_version_id:
            raise ATLValidationError(
                "agent_version_id is required; create an AgentVersion first",
                code="missing_agent_version_id",
            )
        # The backtest environment fixes starting capital (backend INITIAL_CAPITAL).
        _FIXED_INITIAL_CASH = 1_000
        run_config: Dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
        }
        if symbols is not None:
            run_config["symbols"] = symbols
        if config:
            run_config.update(config)
        # Validate the EFFECTIVE value — from the kwarg OR a raw config dict — so
        # config={"initial_cash": ...} can't smuggle a value past the guard, then
        # never put it on the wire (the environment applies its fixed default).
        effective_cash = run_config.get("initial_cash", initial_cash)
        if effective_cash is not None and float(effective_cash) != _FIXED_INITIAL_CASH:
            raise ATLValidationError(
                f"initial_cash is fixed at {_FIXED_INITIAL_CASH:.0f} in this "
                "environment; custom starting capital is not supported",
                code="initial_cash_fixed",
            )
        run_config.pop("initial_cash", None)
        body = {
            "agent_version_id": agent_version_id,
            "environment": {"type": environment_type, "environment_id": environment_id},
            "config": run_config,
        }
        resp = self._request("POST", "/api/v1/runs", body=body)
        return Run.from_dict(resp)

    def get_run(self, run_id: str) -> Run:
        return Run.from_dict(self._request("GET", f"/api/v1/runs/{run_id}"))

    def get_run_status(self, run_id: str) -> RunStatus:
        return RunStatus.from_dict(self._request("GET", f"/api/v1/runs/{run_id}/status"))

    # -- steps -------------------------------------------------------------

    def get_next_step(self, run_id: str) -> Step:
        return Step.from_dict(self._request("GET", f"/api/v1/runs/{run_id}/steps/next"))

    def get_step(self, run_id: str, step_id: str) -> Step:
        return Step.from_dict(self._request("GET", f"/api/v1/runs/{run_id}/steps/{step_id}"))

    def submit_decision(
        self,
        run_id: str,
        step_id: str,
        decision: Union[Decision, Dict[str, Any]],
        *,
        idempotency_key: Optional[str] = None,
    ) -> ExecutionResult:
        """Submit a decision for a step.

        If ``idempotency_key`` is omitted, a deterministic key ``run_id:step_id``
        is used so that retrying the same step reuses the same key.
        """
        key = idempotency_key or f"{run_id}:{step_id}"
        if isinstance(decision, Decision):
            body = decision.to_body(run_id=run_id, step_id=step_id, idempotency_key=key)
        else:
            body = dict(decision)
            body["run_id"] = run_id
            body["step_id"] = step_id
            body["idempotency_key"] = key
            if "orders" in body:
                body["orders"] = [
                    o.to_dict() if isinstance(o, Order) else o for o in body["orders"]
                ]
            else:
                body["orders"] = []
        resp = self._request(
            "POST", f"/api/v1/runs/{run_id}/steps/{step_id}/decision", body=body
        )
        return ExecutionResult.from_dict(resp)

    # -- results / logs ----------------------------------------------------

    def get_run_result(self, run_id: str) -> RunResult:
        return RunResult.from_dict(self._request("GET", f"/api/v1/runs/{run_id}/result"))

    def get_run_trades(self, run_id: str) -> List[Dict[str, Any]]:
        resp = self._request("GET", f"/api/v1/runs/{run_id}/trades")
        return resp.get("trades") or []

    def get_run_decisions(self, run_id: str) -> List[Dict[str, Any]]:
        resp = self._request("GET", f"/api/v1/runs/{run_id}/decisions")
        return resp.get("decisions") or []

    def get_run_metrics(self, run_id: str) -> Dict[str, Any]:
        resp = self._request("GET", f"/api/v1/runs/{run_id}/metrics")
        return resp.get("metrics") or {}

    # -- discovery ---------------------------------------------------------

    def list_environments(self) -> List[Dict[str, Any]]:
        resp = self._request("GET", "/api/v1/environments")
        return resp.get("environments") or []

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def wait(seconds: float) -> None:
        """Sleep for ``seconds`` (used while a run is loading market data)."""
        time.sleep(seconds)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic; never leaks the key
        return f"ATLClient(base_url={self.base_url!r})"


__all__ = ["ATLClient"]
