"""OANDA v20 practice (demo) REST client -- real forex order execution,
gated off by default.

Unlike Tradovate (see infrastructure/brokers/tradovate_paper.py's docstring
for why that client is unverified), OANDA's v20 API genuinely offers free
self-serve practice-account access: a personal access token generated
directly from the Account Management Portal ("My Services" -> "Manage API
Access"), no partner application, no funded minimum. This client is still
unverified against a real OANDA account (no token was available to build
against), but the v20 API shape below is Oanda's own long-stable, thoroughly
publicly documented REST contract, not a guess pieced together from forum
posts the way Tradovate's had to be -- confidence here is meaningfully
higher, though "confidence" is not the same as "verified," so treat the
same way: gated off by TRADOVATE_PAPER_EXECUTE's sibling flag below until
someone with a real practice-account token exercises it end-to-end.

Documented shape (OANDA v20 REST API):
  - Base URL (practice): https://api-fxpractice.oanda.com
  - Auth: Authorization: Bearer {personal_access_token} on every request --
    no separate login/exchange call, unlike Tradovate.
  - Orders: POST {base}/v3/accounts/{accountID}/orders
      body: {"order": {"units": "10000" | "-10000", "instrument": "EUR_USD",
             "timeInForce": "FOK", "type": "MARKET", "positionFill": "DEFAULT"}}
      Units: positive = buy, negative = sell. Instrument uses an underscore,
      not the "EURUSD=X" form Yahoo Finance data uses (see
      infrastructure/market_data/yahoo_forex.py) -- _to_oanda_instrument()
      below converts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

PRACTICE_BASE_URL = "https://api-fxpractice.oanda.com"


class OandaConfigError(RuntimeError):
    """Credentials missing -- raised at client construction, never mid-order."""


class OandaOrderError(RuntimeError):
    """The API rejected the order or the request itself failed."""


@dataclass
class OandaCredentials:
    access_token: str
    account_id: str


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise OandaConfigError(f"{key} must be set to use OANDA execution")
    return value


def credentials_from_env() -> OandaCredentials:
    return OandaCredentials(
        access_token=_require_env("OANDA_ACCESS_TOKEN"),
        account_id=_require_env("OANDA_ACCOUNT_ID"),
    )


def _to_oanda_instrument(yahoo_symbol: str) -> str:
    """"EURUSD=X" -> "EUR_USD". Only meaningful for the 6-letter pairs this
    dashboard's universe is restricted to (see yahoo_forex.py's docstring)."""
    pair = yahoo_symbol.replace("=X", "")
    return f"{pair[:3]}_{pair[3:]}"


class OandaPracticeClient:
    """Practice-account REST client. Every request is best-effort: a failure
    raises OandaOrderError rather than silently doing nothing, since a
    caller reaching this class has already opted into real order submission
    via OANDA_PRACTICE_EXECUTE."""

    def __init__(self, credentials: Optional[OandaCredentials] = None, base_url: str = PRACTICE_BASE_URL):
        self.credentials = credentials or credentials_from_env()
        self.base_url = base_url

    def place_order(self, *, symbol: str, side: str, qty: int) -> Dict[str, Any]:
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")

        instrument = _to_oanda_instrument(symbol)
        units = qty if side == "buy" else -qty
        body = {
            "order": {
                "units": str(units),
                "instrument": instrument,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT",
            }
        }
        url = f"{self.base_url}/v3/accounts/{self.credentials.account_id}/orders"
        try:
            response = requests.post(
                url, json=body,
                headers={"Authorization": f"Bearer {self.credentials.access_token}"}, timeout=10,
            )
        except requests.RequestException as exc:
            raise OandaOrderError(f"OANDA order request failed: {exc}") from exc
        if response.status_code not in (200, 201):
            raise OandaOrderError(f"OANDA order rejected: {response.status_code} {response.text[:300]}")
        return response.json()

    def get_account_summary(self) -> Dict[str, Any]:
        """``GET /v3/accounts/{id}/summary`` -- read-only, no order-placement
        gate needed (unlike place_order, which requires OANDA_PRACTICE_EXECUTE
        upstream). Verified against OANDA's official v20 API docs
        (developer.oanda.com/rest-live-v20/account-ep/): the response's
        ``account`` object carries ``balance`` (cash) and ``NAV`` (net asset
        value, i.e. balance + unrealized P&L across open positions) as
        decimal strings."""
        url = f"{self.base_url}/v3/accounts/{self.credentials.account_id}/summary"
        try:
            response = requests.get(
                url, headers={"Authorization": f"Bearer {self.credentials.access_token}"}, timeout=10,
            )
        except requests.RequestException as exc:
            raise OandaOrderError(f"OANDA account summary request failed: {exc}") from exc
        if response.status_code != 200:
            raise OandaOrderError(f"OANDA account summary failed: {response.status_code} {response.text[:300]}")
        return response.json()["account"]
