"""Alpaca **IRA** broker adapter -- a third, distinct Alpaca account
alongside :mod:`alpaca_paper` and :mod:`alpaca_live`.

An IRA is a genuinely separate brokerage account from Alpaca's regular
taxable live account -- its own account number, its own key pair, real
money, real retirement funds. Alpaca rejects one account's keys against
another account's endpoint the same way paper/live already do, so this
module owns its own credential provider name (``alpaca_ira``) and its own
env vars (``ALPACA_IRA_API_KEY`` / ``ALPACA_IRA_SECRET_KEY``) rather than
reusing ``alpaca_live``'s -- reusing them would mean a saved IRA key
silently overwrites (or gets overwritten by) the regular live account's
key, the exact cross-account mixup this module exists to prevent.

Currently balance-visibility only (domain/wallets.py reads get_account()
here so the IRA's real balance shows up on the Stocks dashboard and the
Home page's combined portfolio total) -- **no order-execution path uses
this client yet**. Manual's Top 10 promote-to-real-money and the Strategy
Catalog's Run in Live button both still only ever place orders through
alpaca_live.py's account; wiring either of those to trade through the IRA
account instead (or in addition) is a separate, not-yet-built decision
about which strategies should be allowed to touch retirement funds, not
something to default into by adding a client.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

from dashboard.backend.infrastructure.brokers.credentials import resolve_alpaca_credentials
from dashboard.backend.paths import CREDENTIALS_DIR


class AlpacaIRACredentialsError(RuntimeError):
    """Raised when IRA credentials are missing or malformed."""


@dataclass
class IRAOrderResult:
    order_id: Optional[str]
    status: Optional[str]
    symbol: str
    side: str
    qty: float
    raw: Dict[str, Any]


class AlpacaIRATradingClient:
    """Interface to Alpaca's IRA account (real money, a distinct account
    from the regular live account).

    Credentials are intentionally read from ``ALPACA_IRA_API_KEY`` /
    ``ALPACA_IRA_SECRET_KEY`` (or ``credentials/alpaca_ira.json``) -- never
    ``ALPACA_LIVE_API_KEY``'s pair, so a saved IRA key can never silently
    stand in for (or be overwritten by) the regular live account's key.
    """

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None,
                 user_id: Optional[int] = None):
        self.api_key = api_key
        self.secret_key = secret_key
        if not self.api_key or not self.secret_key:
            resolved = resolve_alpaca_credentials(user_id, "alpaca_ira")
            if resolved:
                self.api_key, self.secret_key = resolved
        if not self.api_key or not self.secret_key:
            self._load_from_credentials()
        if not self.api_key or not self.secret_key:
            raise AlpacaIRACredentialsError(
                "Alpaca IRA credentials not configured. Set ALPACA_IRA_API_KEY / "
                "ALPACA_IRA_SECRET_KEY in .env, or create credentials/alpaca_ira.json "
                "(see credentials/alpaca_live.json.example for the shape)."
            )

        self._trading = TradingClient(self.api_key, self.secret_key, paper=False)
        self._data = StockHistoricalDataClient(self.api_key, self.secret_key)

    def _load_from_credentials(self) -> None:
        self.api_key = self.api_key or os.getenv("ALPACA_IRA_API_KEY")
        self.secret_key = self.secret_key or os.getenv("ALPACA_IRA_SECRET_KEY")
        if self.api_key and self.secret_key:
            return

        creds_path = CREDENTIALS_DIR / "alpaca_ira.json"
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
                "portfolio_value": float(account.portfolio_value),
                "account_number": account.account_number,
                "account_status": str(account.status),
                "pattern_day_trader": bool(account.pattern_day_trader),
                "trading_blocked": bool(account.trading_blocked),
            }
        except Exception as e:
            print(f"Exception in AlpacaIRATradingClient.get_account: {e}")
            return None

    def get_positions(self) -> Dict[str, float]:
        """Return ``{symbol: qty}`` for every held position (long only is expected)."""
        try:
            positions = self._trading.get_all_positions()
            return {p.symbol.upper(): float(p.qty) for p in positions}
        except Exception as e:
            print(f"Exception in AlpacaIRATradingClient.get_positions: {e}")
            return {}

    def get_positions_detailed(self) -> List[Dict[str, Any]]:
        try:
            positions = self._trading.get_all_positions()
            return [
                {
                    "symbol": p.symbol.upper(),
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price) if p.current_price else None,
                    "market_value": float(p.market_value) if p.market_value else None,
                    "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else None,
                    "unrealized_plpc": float(p.unrealized_plpc) if p.unrealized_plpc else None,
                    "side": str(p.side),
                }
                for p in positions
            ]
        except Exception as e:
            print(f"Exception in AlpacaIRATradingClient.get_positions_detailed: {e}")
            return []

    def get_quotes(self, symbols: List[str]) -> Dict[str, float]:
        if not symbols:
            return {}
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=symbols)
            quotes = self._data.get_stock_latest_quote(request)
        except Exception as e:
            print(f"Exception in AlpacaIRATradingClient.get_quotes: {e}")
            return {}

        prices: Dict[str, float] = {}
        for symbol, quote in quotes.items():
            price = getattr(quote, "ask_price", None) or getattr(quote, "bid_price", None)
            if price and price > 0:
                prices[symbol.upper()] = float(price)
        return prices

    def submit_market_order(self, symbol: str, qty: float, side: str) -> IRAOrderResult:
        """Not called from anywhere yet -- see module docstring. Kept for
        parity with alpaca_live.py so a future execution path (once someone
        deliberately decides IRA funds should be tradeable, not just
        visible) has a ready client rather than a half-built one."""
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
        order = self._trading.submit_order(order_data=request)
        return IRAOrderResult(
            order_id=str(order.id) if order.id else None,
            status=str(order.status) if order.status else None,
            symbol=symbol,
            side=side,
            qty=qty,
            raw={
                "id": str(order.id),
                "status": str(order.status),
                "symbol": order.symbol,
                "qty": str(order.qty),
                "side": str(order.side),
                "submitted_at": str(order.submitted_at),
            },
        )
