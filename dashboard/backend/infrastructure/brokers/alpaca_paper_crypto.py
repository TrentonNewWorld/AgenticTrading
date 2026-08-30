"""Alpaca **crypto** paper-trading broker adapter.

New sibling module, following this repo's established convention exactly
(alpaca_paper.py is the equities client, alpaca_paper_options.py is the
options client, this is the crypto client). Crypto trading uses the SAME
Alpaca account and API key/secret as equities and options -- confirmed
against a real paper account (2026-08-23 spike): crypto_status ACTIVE, 73
tradable crypto assets, all fractionable, BTC/USD included -- so this client
shares the Connections page's existing ``alpaca_paper`` provider entry
rather than needing its own, same as alpaca_paper_options.py.

Unlike alpaca_paper_options.py's MLEG orders (limit-only, time-gated to
regular hours on Alpaca's options book), a crypto market order is accepted
any time -- crypto trades 24/7, there is no "regular hours" concept to gate
against. Orders use MarketOrderRequest + TimeInForce.GTC, Alpaca's own
documented convention for crypto (DAY has no meaning without a trading-day
boundary).

Real order submission is still gated off by default
(``ALPACA_PAPER_CRYPTO_EXECUTE``, see domain/crypto/engine.py) -- matching
every other dashboard's dry-run-by-default posture even though, unlike
Tradovate/OANDA, this broker connection genuinely was verified working end
to end. Consistency with the rest of the app's real-money-adjacent gates
matters more here than "we could, so we should."
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from dashboard.backend.infrastructure.brokers.credentials import resolve_alpaca_credentials
from dashboard.backend.paths import CREDENTIALS_DIR


@dataclass
class CryptoOrderResult:
    order_id: Optional[str]
    status: Optional[str]
    symbol: str
    raw: Dict[str, Any]


class AlpacaPaperCryptoClient:
    """Interface to Alpaca's crypto paper-trading API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        user_id: Optional[int] = None,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        if not self.api_key or not self.secret_key:
            resolved = resolve_alpaca_credentials(user_id, "alpaca_paper")
            if resolved:
                self.api_key, self.secret_key = resolved
        if not self.api_key or not self.secret_key:
            self._load_from_credentials()
        if not self.api_key or not self.secret_key:
            raise FileNotFoundError(
                "Alpaca credentials not configured (ALPACA_API_KEY/ALPACA_SECRET_KEY, "
                "credentials/alpaca.json, or a saved Connections entry)."
            )

        from alpaca.trading.client import TradingClient

        self._trading = TradingClient(self.api_key, self.secret_key, paper=True)

    def _load_from_credentials(self) -> None:
        import os

        self.api_key = self.api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = self.secret_key or os.getenv("ALPACA_SECRET_KEY")
        if self.api_key and self.secret_key:
            return
        creds_path = CREDENTIALS_DIR / "alpaca.json"
        if creds_path.exists():
            with open(creds_path, "r") as f:
                creds = json.load(f)
                self.api_key = self.api_key or creds.get("api_key") or creds.get("apiKey")
                self.secret_key = self.secret_key or creds.get("secret_key") or creds.get("secretKey")

    def get_account(self) -> Optional[Dict[str, Any]]:
        try:
            account = self._trading.get_account()
            return {
                "cash": float(account.cash),
                "equity": float(account.equity),
                "buying_power": float(account.buying_power),
                "crypto_status": str(getattr(account, "crypto_status", None)),
            }
        except Exception as e:
            print(f"Exception in AlpacaPaperCryptoClient.get_account: {e}")
            return None

    def submit_crypto_order(self, *, symbol: str, side: str, qty: float) -> Optional[CryptoOrderResult]:
        """Submit a fractional-quantity crypto market order. Deliberately
        market, not limit -- unlike an options combo (where a market order
        risks a much worse fill on a multi-leg spread), a single-instrument
        crypto market order on a liquid major pair fills close to the
        quoted price, and callers here already computed their own qty from
        a fresh quote."""
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        try:
            order = self._trading.submit_order(
                order_data=MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                    time_in_force=TimeInForce.GTC,
                )
            )
        except Exception as e:
            print(f"Exception in AlpacaPaperCryptoClient.submit_crypto_order: {e}")
            return None

        return CryptoOrderResult(
            order_id=str(order.id) if order.id else None,
            status=str(order.status) if order.status else None,
            symbol=symbol,
            raw={"id": str(order.id), "status": str(order.status)},
        )

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._trading.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            print(f"Exception in AlpacaPaperCryptoClient.cancel_order: {e}")
            return False
