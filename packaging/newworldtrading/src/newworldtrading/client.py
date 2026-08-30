"""A small, dependency-free client for the NewWorldTrading REST API.

The client uses only the Python standard library (``urllib``) so that
``pip install newworldtrading`` stays lightweight and has no transitive
dependencies. It covers the public read endpoints (health, runs, leaderboard,
ticker, paper trading) and the agent-facing backtest workflow
(``/api/v1/backtest/...``).

Example
-------
>>> from newworldtrading import NewWorldTradingClient
>>> client = NewWorldTradingClient("https://newworldtrading.onrender.com")
>>> client.health()
{'status': 'ok', ...}

Driving a backtest with your own strategy::

    client = NewWorldTradingClient(api_key="ag_xxx")

    def strategy(snapshot):
        # return a list of action dicts, e.g.
        return [{"action": "hold", "symbol": "AAPL", "confidence": 0.5,
                 "reasoning": "no signal", "position_size": 0}]

    result = client.run_backtest("2026-04-15", "2026-04-16", strategy,
                                 agent_name="my-agent", model_name="rule-based")
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

DEFAULT_BASE_URL = "https://newworldtrading.onrender.com"


class ApiError(RuntimeError):
    """Raised when the API returns a non-2xx response."""

    def __init__(self, status: int, url: str, detail: Any):
        self.status = status
        self.url = url
        self.detail = detail
        super().__init__(f"HTTP {status} {url}: {detail}")


# Strategy callback: receives a market snapshot dict, returns a list of actions.
Strategy = Callable[[dict], list]


class NewWorldTradingClient:
    """Thin HTTP client for an NewWorldTrading API server.

    Parameters
    ----------
    base_url:
        Base URL of the API server (default: the hosted demo backend).
    api_key:
        Registered agent API key (``ag_...``). When provided, :meth:`resolve`
        is used to obtain the session id automatically.
    session_id:
        Dashboard session id, sent as the ``X-Session-Id`` header. Optional if
        ``api_key`` is supplied.
    timeout:
        Default per-request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session_id = session_id
        self.timeout = timeout

    # -- low-level ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"

        headers = {"Accept": "application/json"}
        if self.session_id:
            headers["X-Session-Id"] = self.session_id
        if self.api_key:
            headers["X-API-Key"] = self.api_key

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
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                detail = parsed.get("detail", parsed) if isinstance(parsed, dict) else parsed
            except json.JSONDecodeError:
                pass
            raise ApiError(exc.code, url, detail) from exc
        except TimeoutError as exc:
            raise ApiError(0, url, f"request timed out after {timeout or self.timeout}s") from exc

    # -- public read endpoints --------------------------------------------

    def health(self) -> dict:
        """Return server health status."""
        return self._request("GET", "/health")

    def config_defaults(self) -> dict:
        """Return default run ids and date range configured on the server."""
        return self._request("GET", "/config/defaults")

    def ticker(self, symbols: str = "AAPL,NVDA,MSFT,BTC") -> dict:
        """Return latest quotes for a comma-separated list of symbols."""
        return self._request("GET", "/ticker", params={"symbols": symbols})

    def runs(self, mode: Optional[str] = None) -> list:
        """List stored backtest runs (optionally filtered by ``mode``)."""
        return self._request("GET", "/runs", params={"mode": mode})

    def run(self, run_id: str) -> dict:
        """Return metadata for a single run."""
        return self._request("GET", f"/runs/{run_id}")

    def equity(self, run_id: str) -> dict:
        """Return the equity curve for a run."""
        return self._request("GET", f"/runs/{run_id}/equity")

    def compare(self, run_ids: str) -> dict:
        """Compare equity curves for a comma-separated list of run ids."""
        return self._request("GET", "/compare", params={"run_ids": run_ids})

    def leaderboard(self) -> Any:
        """Return the agent leaderboard."""
        return self._request("GET", "/api/v1/leaderboard")

    def paper_account(self) -> dict:
        """Return the paper-trading account summary."""
        return self._request("GET", "/paper/account")

    def paper_positions(self) -> Any:
        """Return current paper-trading positions."""
        return self._request("GET", "/paper/positions")

    def paper_trades(self, limit: int = 50) -> Any:
        """Return recent paper-trading trades."""
        return self._request("GET", "/paper/trades", params={"limit": limit})

    # -- agent auth --------------------------------------------------------

    def resolve(self) -> dict:
        """Resolve an agent API key into a session.

        Requires ``api_key``. On success, ``self.session_id`` is populated.
        """
        if not self.api_key:
            raise ValueError("resolve() requires an api_key")
        resolved = self._request("GET", "/api/v1/agents/resolve", timeout=60)
        if isinstance(resolved, dict) and resolved.get("session_id"):
            self.session_id = resolved["session_id"]
        return resolved

    # -- backtest workflow -------------------------------------------------

    def backtest_schema(self) -> dict:
        """Return the decision schema and timeouts for external backtests."""
        return self._request("GET", "/api/v1/backtest/schema")

    def start_backtest(
        self,
        start_date: str,
        end_date: str,
        agent_name: str = "external-agent",
        model_name: str = "custom",
        mode: str = "safe_trading",
    ) -> dict:
        """Start a new external backtest and return its descriptor."""
        return self._request(
            "POST",
            "/api/v1/backtest/start",
            body={
                "start_date": start_date,
                "end_date": end_date,
                "agent_name": agent_name,
                "model_name": model_name,
                "mode": mode,
            },
            timeout=60,
        )

    def current_step(self, backtest_id: str) -> dict:
        """Return the current step / market context for a running backtest."""
        return self._request("GET", f"/api/v1/backtest/{backtest_id}/steps/current")

    def submit_decisions(self, backtest_id: str, actions: list) -> dict:
        """Submit trading decisions for the current step."""
        return self._request(
            "POST",
            f"/api/v1/backtest/{backtest_id}/steps/current/decisions",
            body={"actions": actions},
        )

    def backtest_status(self, backtest_id: str) -> dict:
        """Return the status of a backtest."""
        return self._request("GET", f"/api/v1/backtest/{backtest_id}/status")

    def run_result(self, run_id: str) -> dict:
        """Return the full result (trades + decisions) for a finished run."""
        return self._request("GET", f"/api/v1/backtest/runs/{run_id}/result")

    # -- high-level convenience -------------------------------------------

    def run_backtest(
        self,
        start_date: str,
        end_date: str,
        strategy: Strategy,
        agent_name: str = "external-agent",
        model_name: str = "custom",
        mode: str = "safe_trading",
        poll_interval: float = 2.0,
        on_step: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """Run a full backtest loop, calling ``strategy`` at each step.

        ``strategy`` receives the per-step market snapshot (a dict) and must
        return a list of action dicts. The loop submits those decisions, polls
        until the backtest completes, and returns the final step context.

        If ``api_key`` is set but no session yet, this resolves it first.
        """
        if self.api_key and not self.session_id:
            self.resolve()

        started = self.start_backtest(start_date, end_date, agent_name, model_name, mode)
        backtest_id = started["backtest_id"]

        while True:
            ctx = self.current_step(backtest_id)
            status = ctx.get("status")

            if status == "loading":
                time.sleep(poll_interval)
                continue
            if status == "completed":
                return ctx
            if status == "failed":
                raise ApiError(0, f"/api/v1/backtest/{backtest_id}", ctx.get("error", "backtest failed"))
            if status != "waiting_decision":
                time.sleep(min(poll_interval, 0.5))
                continue

            if on_step is not None:
                on_step(ctx)

            actions = strategy(ctx.get("market_snapshot") or {})
            result = self.submit_decisions(backtest_id, actions)
            if isinstance(result, dict) and result.get("status") == "completed":
                return result

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"NewWorldTradingClient(base_url={self.base_url!r})"
