"""Polymarket real-money order execution -- gated off by default, real money
only, no demo mode.

Polymarket has no demo/sandbox mode at all (verified live this session
against its public docs: only production endpoints exist). Every order this
client places is real money against a real funded Polygon wallet -- unlike
Kalshi, there is no free/fake-money mode to fall back to for "realistic"
fills. Prediction's paper trading always simulates Polymarket locally
against public market data instead (see
``infrastructure/market_data/polymarket_markets.py``); this client is the
real-money path only, reached solely through BOTH an explicit
``POLYMARKET_EXECUTE`` operator opt-in AND the calling user's own connected
wallet key -- the same double-gate (operator flag + per-user credential)
this repo already uses for its other real-money features (see
``ATL_STRIPE_TEST_BILLING_ENABLED`` in CLAUDE.md for the pattern), because
unlike Kalshi's demo path there is no fake-money floor under a mistake here.

Order construction/signing uses Polymarket's own official ``py-clob-client``
library (PyPI), not hand-rolled EIP-712: placing an order needs a SECOND,
contract-specific EIP-712 signature over the CTF Exchange's order struct
(domain separator, verifying contract address, field encoding) beyond the
simpler L1 auth-derivation signature -- getting that exactly right by hand
from documentation fragments is exactly the kind of mistake that either
silently fails (safe) or, done wrong in a way that still validates, risks
real funds. The official client is community-maintained by Polymarket
itself and is what real integrations use.

Verified working this session (2026-08-24), end to end except the final
submit, using a well-known deterministic test private key (never used for
anything real) against Polymarket's real production API: client
construction, tick-size resolution against a real live market, and full
EIP-712 order signing all completed successfully. Only the final
``post_order`` network call was not exercised (would require a funded
wallet to settle against), so it is still marked unverified below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

PRODUCTION_CLOB_BASE_URL = "https://clob.polymarket.com"
#: Polymarket operates on Polygon mainnet only.
POLYGON_CHAIN_ID = 137


class PolymarketConfigError(RuntimeError):
    """Credentials missing -- raised at client construction, never mid-order."""


class PolymarketOrderError(RuntimeError):
    """The API rejected the order or the request itself failed."""


@dataclass
class PolymarketCredentials:
    wallet_private_key: str


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise PolymarketConfigError(f"{key} must be set to use Polymarket execution")
    return value


def credentials_from_env() -> PolymarketCredentials:
    return PolymarketCredentials(wallet_private_key=_require_env("POLYMARKET_WALLET_PRIVATE_KEY"))


def execute_enabled() -> bool:
    """The operator-level half of the double-gate -- read fresh on every
    call (not cached at import time), same convention as every other
    ``*_EXECUTE``/``*_ENABLED`` flag in this repo, so a config change takes
    effect without a redeploy and the test suite's stripped environment
    never accidentally arms real-money execution."""
    return os.getenv("POLYMARKET_EXECUTE", "false").strip().lower() in {"1", "true", "yes", "on"}


class PolymarketClient:
    """UNVERIFIED against a real funded wallet's final order submission --
    see module docstring for exactly what was and wasn't exercised this
    session. Everything up to (not including) the network POST to place the
    order was confirmed working against Polymarket's real API."""

    def __init__(self, credentials: Optional[PolymarketCredentials] = None, base_url: str = PRODUCTION_CLOB_BASE_URL):
        self.credentials = credentials or credentials_from_env()
        self.base_url = base_url
        self._client = None  # lazily constructed -- see _clob_client()

    def _clob_client(self):
        """Lazy import + construction: importing this module must never
        require py-clob-client (and its dependency tree) to be installed
        just to read the class definitions or check execute_enabled()."""
        if self._client is None:
            from py_clob_client.client import ClobClient

            client = ClobClient(self.base_url, key=self.credentials.wallet_private_key, chain_id=POLYGON_CHAIN_ID)
            client.set_api_creds(client.create_or_derive_api_creds())
            self._client = client
        return self._client

    def place_order(self, *, token_id: str, side: str, size: float, price: float) -> Dict[str, Any]:
        """``token_id``: the outcome's CLOB token id (from a Gamma market's
        ``clobTokenIds``). ``side``: "buy" | "sell". ``size``: positive
        number of outcome shares. ``price``: 0-1 limit price."""
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        if size <= 0:
            raise ValueError(f"size must be positive, got {size}")
        if not (0.0 < price < 1.0):
            raise ValueError(f"price must be between 0 and 1 (exclusive), got {price}")

        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.exceptions import PolyApiException
        from py_clob_client.order_builder.constants import BUY, SELL

        try:
            client = self._clob_client()
            order_args = OrderArgs(
                price=price, size=size, side=BUY if side == "buy" else SELL, token_id=token_id,
            )
            signed_order = client.create_order(order_args)
            response = client.post_order(signed_order)
        except PolyApiException as exc:
            raise PolymarketOrderError(f"Polymarket order rejected: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 -- network/library errors, not just the typed API one
            raise PolymarketOrderError(f"Polymarket order request failed: {exc}") from exc
        return response
