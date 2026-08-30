"""Tradovate demo/simulation REST client -- real futures order execution,
gated off by default.

⚠ UNVERIFIED AGAINST A LIVE TRADOVATE ACCOUNT. Written from Tradovate's
publicly documented REST API shape, not from a live spike the way every other
broker client in this repo was built (``infrastructure/brokers/alpaca_paper.py``
etc. were all exercised against real paper credentials first). The spike for
*this* broker (2026-08-23) found that getting real Tradovate API credentials
requires either a $1,000+ funded account plus monthly API fees, or a formal
NinjaTrader-Ecosystem partner application -- neither available to build
against -- so this module could not be live-tested the way this repo's other
broker clients were. Per the "fail-closed is not fail-visible" principle
(see CLAUDE.md), that gap is documented here rather than hidden: if the wire
format below turns out to be wrong, ``TRADOVATE_PAPER_EXECUTE`` staying unset
by default (see below) means nobody hits it until someone with real
credentials verifies and fixes it.

Until then, ``domain/futures/engine.py`` runs entirely on simulated fills
priced from free Yahoo Finance data (``infrastructure/market_data/
yahoo_futures.py``) -- this client is wired into that engine's execute path
but only reached when ``TRADOVATE_PAPER_EXECUTE`` is truthy AND real
credentials are configured (Connections store or env vars), mirroring every
other real-money-adjacent gate in this codebase (``ALPACA_PAPER_EXECUTE``,
``ALPACA_PAPER_OPTIONS_EXECUTE``).

Documented shape (Tradovate REST API, "Access Track"):
  - Auth: POST {base}/auth/accesstokenrequest
      body: {name, password, appId, appVersion, cid, sec}
      response: {accessToken, expirationTime, ...}
  - Orders: POST {base}/order/placeorder
      body: {accountSpec, accountId, action: "Buy"|"Sell", symbol,
             orderQty, orderType: "Market", timeInForce: "Day",
             isAutomated: true}
      Authorization: Bearer {accessToken}
  - Demo base URL: https://demo.tradovateapi.com/v1
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

DEMO_BASE_URL = "https://demo.tradovateapi.com/v1"
_APP_ID = "NewWorldTrading"
_APP_VERSION = "1.0"
_TOKEN_SAFETY_MARGIN_SECONDS = 60


class TradovateConfigError(RuntimeError):
    """Credentials missing or incomplete -- raised at client construction,
    never mid-order, so a misconfiguration surfaces immediately."""


class TradovateOrderError(RuntimeError):
    """The API rejected the order or the request itself failed."""


@dataclass
class TradovateCredentials:
    name: str
    password: str
    cid: str
    sec: str
    account_spec: str
    account_id: int


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise TradovateConfigError(f"{key} must be set to use Tradovate execution")
    return value


def credentials_from_env() -> TradovateCredentials:
    return TradovateCredentials(
        name=_require_env("TRADOVATE_USERNAME"),
        password=_require_env("TRADOVATE_PASSWORD"),
        cid=_require_env("TRADOVATE_CID"),
        sec=_require_env("TRADOVATE_SECRET"),
        account_spec=_require_env("TRADOVATE_ACCOUNT_SPEC"),
        account_id=int(_require_env("TRADOVATE_ACCOUNT_ID")),
    )


class TradovatePaperClient:
    """Demo/simulation-account REST client. Every request is best-effort:
    a failure raises TradovateOrderError rather than silently doing nothing,
    since a caller reaching this class has already opted into real order
    submission via TRADOVATE_PAPER_EXECUTE."""

    def __init__(self, credentials: Optional[TradovateCredentials] = None, base_url: str = DEMO_BASE_URL):
        self.credentials = credentials or credentials_from_env()
        self.base_url = base_url
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def _authenticate(self) -> str:
        creds = self.credentials
        body = {
            "name": creds.name, "password": creds.password,
            "appId": _APP_ID, "appVersion": _APP_VERSION,
            "cid": creds.cid, "sec": creds.sec,
        }
        try:
            response = requests.post(f"{self.base_url}/auth/accesstokenrequest", json=body, timeout=10)
        except requests.RequestException as exc:
            raise TradovateOrderError(f"Tradovate auth request failed: {exc}") from exc
        if response.status_code != 200:
            raise TradovateOrderError(f"Tradovate auth failed: {response.status_code} {response.text[:200]}")
        data = response.json()
        token = data.get("accessToken")
        if not token:
            raise TradovateOrderError(f"Tradovate auth response had no accessToken: {data}")
        self._access_token = token
        # expirationTime is documented as an ISO timestamp; a fixed 20-minute
        # cache is used instead of parsing it, since the exact field format is
        # one of the pieces this module could not verify live (see module
        # docstring) -- a short, conservative cache re-authenticates often
        # enough to never trade on a token past its real expiry either way.
        self._token_expires_at = time.time() + (20 * 60) - _TOKEN_SAFETY_MARGIN_SECONDS
        return token

    def _token(self) -> str:
        if self._access_token is None or time.time() >= self._token_expires_at:
            return self._authenticate()
        return self._access_token

    def place_order(self, *, symbol: str, side: str, qty: int) -> Dict[str, Any]:
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")

        token = self._token()
        body = {
            "accountSpec": self.credentials.account_spec,
            "accountId": self.credentials.account_id,
            "action": "Buy" if side == "buy" else "Sell",
            "symbol": symbol,
            "orderQty": qty,
            "orderType": "Market",
            "timeInForce": "Day",
            "isAutomated": True,
        }
        try:
            response = requests.post(
                f"{self.base_url}/order/placeorder", json=body,
                headers={"Authorization": f"Bearer {token}"}, timeout=10,
            )
        except requests.RequestException as exc:
            raise TradovateOrderError(f"Tradovate order request failed: {exc}") from exc
        if response.status_code != 200:
            raise TradovateOrderError(f"Tradovate order rejected: {response.status_code} {response.text[:300]}")
        return response.json()

    def get_cash_balance(self) -> Dict[str, Any]:
        """``POST /cashBalance/getcashbalancesnapshot`` -- deliberately named
        "cash balance", not "account value"/"net liq": Tradovate's community
        docs (community.tradovate.com/t/understanding-the-need-to-calculate-
        account-performance-values/2116) confirm the API returns raw balance
        data only, with no pre-computed net-liquidating-value field -- that
        would require combining this with open-position P&L via a separate
        endpoint, which this method deliberately does not attempt (a wrong
        computed "total" would be worse than an honestly-labeled cash
        figure). Callers displaying this should label it "cash balance," not
        "account value.\""""
        token = self._token()
        try:
            response = requests.post(
                f"{self.base_url}/cashBalance/getcashbalancesnapshot",
                json={"accountId": self.credentials.account_id},
                headers={"Authorization": f"Bearer {token}"}, timeout=10,
            )
        except requests.RequestException as exc:
            raise TradovateOrderError(f"Tradovate cash balance request failed: {exc}") from exc
        if response.status_code != 200:
            raise TradovateOrderError(
                f"Tradovate cash balance request failed: {response.status_code} {response.text[:300]}"
            )
        return response.json()
