"""Kalshi demo-exchange order execution.

UNVERIFIED AGAINST A LIVE ACCOUNT: no Kalshi credentials were available to
build this against, so the request/signing shape below follows Kalshi's
publicly documented RSA-PSS auth scheme but has not been exercised end to
end.

Wired into ``domain/prediction/engine.py``'s ``_place_real_kalshi_order``:
connecting a demo-account key via Connections auto-upgrades Prediction's
Kalshi paper fills from local simulation to the free demo exchange, with no
separate execute flag -- unlike Polymarket's real-money path, demo fills are
fake money, so there is no mistake here that flag would be protecting
against. Only ``environment="demo"`` is ever reached from the app today: no
call site passes ``environment="production"``, so a connected *production*
key is inert (the client would sign requests correctly, but the app never
targets the production host). Wiring up real production trading -- a real
money feature -- would need its own explicit operator gate first, matching
this repo's ``POLYMARKET_EXECUTE`` convention; that has not been built.

Two distinct things this module deliberately does NOT confuse (see
``domain/prediction/``'s engine for how the split is used):

* **Kalshi's free demo exchange** (base URL ``demo-api.kalshi.co``) is a
  genuine parallel copy of the real exchange with fake money -- orders here
  fill against real demo order flow, not a local simulation. Sign-up is free
  and needs no real personal info (verified live via Kalshi's own help
  docs this session: "you can just use mock information... no real personal
  details needed"). This client's default target.
* **Kalshi's production exchange** (base URL from ``KALSHI_BASE_URL_OVERRIDE``,
  defaulting to the real trade-api host) is real money. A caller must pass
  ``environment="production"`` explicitly -- there is no way to reach it by
  accident through this module's defaults.

Auth (RSA-PSS, documented Kalshi scheme, both environments):
  1. Generate an RSA keypair once; upload the public key via Kalshi's
     dashboard to get a Key ID (``KALSHI_API_KEY_ID``).
  2. Sign every request: ``message = f"{timestamp_ms}{method}{path}"``,
     PSS-padded SHA256 digest, signed with the private key
     (``KALSHI_PRIVATE_KEY_PEM``), base64-encoded into the
     ``KALSHI-ACCESS-SIGNATURE`` header alongside ``KALSHI-ACCESS-KEY`` and
     ``KALSHI-ACCESS-TIMESTAMP``.

Public market data needs none of this -- see
``infrastructure/market_data/kalshi_markets.py``, which this client does not
duplicate.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
PRODUCTION_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiConfigError(RuntimeError):
    """Credentials missing -- raised at client construction, never mid-order."""


class KalshiOrderError(RuntimeError):
    """The API rejected the order or the request itself failed."""


@dataclass
class KalshiCredentials:
    api_key_id: str
    private_key_pem: str


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise KalshiConfigError(f"{key} must be set to use Kalshi execution")
    return value


def credentials_from_env() -> KalshiCredentials:
    return KalshiCredentials(
        api_key_id=_require_env("KALSHI_API_KEY_ID"),
        private_key_pem=_require_env("KALSHI_PRIVATE_KEY_PEM"),
    )


def _sign(private_key_pem: str, message: str) -> str:
    """RSA-PSS-SHA256 signature, base64-encoded -- Kalshi's documented
    request-signing scheme. Imports ``cryptography`` lazily so importing
    this module never requires it just to read the class definitions."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


class KalshiClient:
    """Order-placement client for either the demo or production exchange --
    see module docstring for why they're the same client with a base-URL
    switch rather than two classes: the request/signing shape is identical,
    only the money behind it differs."""

    def __init__(
        self,
        credentials: Optional[KalshiCredentials] = None,
        *,
        environment: str = "demo",
    ):
        if environment not in ("demo", "production"):
            raise ValueError(f"environment must be 'demo' or 'production', got {environment!r}")
        self.environment = environment
        self.base_url = DEMO_BASE_URL if environment == "demo" else PRODUCTION_BASE_URL
        self.credentials = credentials or credentials_from_env()

    def _headers(self, method: str, path: str) -> Dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method}{path}"
        signature = _sign(self.credentials.private_key_pem, message)
        return {
            "KALSHI-ACCESS-KEY": self.credentials.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

    def place_order(self, *, ticker: str, side: str, action: str, count: int) -> Dict[str, Any]:
        """``side``: "yes" | "no". ``action``: "buy" | "sell". ``count``:
        positive number of contracts."""
        if side not in ("yes", "no"):
            raise ValueError(f"side must be 'yes' or 'no', got {side!r}")
        if action not in ("buy", "sell"):
            raise ValueError(f"action must be 'buy' or 'sell', got {action!r}")
        if count <= 0:
            raise ValueError(f"count must be positive, got {count}")

        path = "/trade-api/v2/portfolio/orders"
        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": "market",
        }
        try:
            response = requests.post(
                f"{self.base_url}/portfolio/orders", json=body,
                headers=self._headers("POST", path), timeout=10,
            )
        except requests.RequestException as exc:
            raise KalshiOrderError(f"Kalshi order request failed: {exc}") from exc
        if response.status_code not in (200, 201):
            raise KalshiOrderError(f"Kalshi order rejected: {response.status_code} {response.text[:300]}")
        return response.json()

    def get_balance(self) -> Dict[str, Any]:
        """``GET /portfolio/balance`` -- read-only, still needs the same
        RSA-PSS signed request as order placement (Kalshi has no unsigned
        account-data reads). Verified against Kalshi's official API docs
        (docs.kalshi.com/api-reference/portfolio/get-balance): the response
        carries ``balance`` (cents, int), ``balance_dollars`` (decimal
        string), and ``portfolio_value`` (cents, int -- balance plus the
        mark-to-market value of open positions)."""
        path = "/trade-api/v2/portfolio/balance"
        try:
            response = requests.get(
                f"{self.base_url}/portfolio/balance", headers=self._headers("GET", path), timeout=10,
            )
        except requests.RequestException as exc:
            raise KalshiOrderError(f"Kalshi balance request failed: {exc}") from exc
        if response.status_code != 200:
            raise KalshiOrderError(f"Kalshi balance request failed: {response.status_code} {response.text[:300]}")
        return response.json()
