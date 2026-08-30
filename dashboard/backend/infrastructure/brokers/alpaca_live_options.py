"""Alpaca **options live-money** broker adapter.

Sub-phase 4 of the Options-dashboard plan. Mirrors alpaca_paper_options.py's
shape exactly, the same way alpaca_live.py mirrors alpaca_paper.py -- separate
module, separate live credentials (``ALPACA_LIVE_API_KEY``/
``ALPACA_LIVE_SECRET_KEY``, never the paper pair), no shared state with the
paper client. This module is a thin broker adapter only, matching
alpaca_live.py's own scope: the risk gate and the real-order execute switch
live one layer up, in the options engine/service (Sub-phase 5), the same way
alpaca_live.py's docstring says its own gates live in
``execution.alpaca_live_service``, not in the client itself.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from dashboard.backend.infrastructure.brokers.alpaca_paper_options import OptionLeg, OptionOrderResult
from dashboard.backend.infrastructure.brokers.credentials import resolve_alpaca_credentials
from dashboard.backend.paths import CREDENTIALS_DIR


class AlpacaLiveOptionsCredentialsError(RuntimeError):
    """Raised when live options credentials are missing or malformed."""


class AlpacaLiveOptionsClient:
    """Interface to Alpaca's **live** options trading API (real money)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        user_id: Optional[int] = None,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        if not self.api_key or not self.secret_key:
            resolved = resolve_alpaca_credentials(user_id, "alpaca_live")
            if resolved:
                self.api_key, self.secret_key = resolved
        if not self.api_key or not self.secret_key:
            self._load_from_credentials()
        if not self.api_key or not self.secret_key:
            raise AlpacaLiveOptionsCredentialsError(
                "Alpaca live credentials not configured. Set ALPACA_LIVE_API_KEY / "
                "ALPACA_LIVE_SECRET_KEY in .env, create credentials/alpaca_live.json, "
                "or save a live connection on the Connections page."
            )

        from alpaca.trading.client import TradingClient

        self._trading = TradingClient(self.api_key, self.secret_key, paper=False)

    def _load_from_credentials(self) -> None:
        import os

        self.api_key = self.api_key or os.getenv("ALPACA_LIVE_API_KEY")
        self.secret_key = self.secret_key or os.getenv("ALPACA_LIVE_SECRET_KEY")
        if self.api_key and self.secret_key:
            return
        creds_path = CREDENTIALS_DIR / "alpaca_live.json"
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
                "options_buying_power": float(getattr(account, "options_buying_power", 0) or 0),
                "options_approved_level": getattr(account, "options_approved_level", None),
            }
        except Exception as e:
            print(f"Exception in AlpacaLiveOptionsClient.get_account: {e}")
            return None

    def get_option_positions(self) -> List[Dict[str, Any]]:
        try:
            from dashboard.backend.infrastructure.market_data.alpaca_options import (
                OptionSymbolError, parse_occ_symbol,
            )

            positions = self._trading.get_all_positions()
            result = []
            for p in positions:
                try:
                    parse_occ_symbol(p.symbol)
                except OptionSymbolError:
                    continue
                result.append({
                    "symbol": p.symbol.upper(),
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price) if p.current_price else None,
                    "market_value": float(p.market_value) if p.market_value else None,
                    "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else None,
                    "side": str(p.side),
                })
            return result
        except Exception as e:
            print(f"Exception in AlpacaLiveOptionsClient.get_option_positions: {e}")
            return []

    def submit_option_order(
        self, legs: List[OptionLeg], *, limit_price: float, qty: int = 1
    ) -> Optional[OptionOrderResult]:
        """Same shape and same limit-only reasoning as
        AlpacaPaperOptionsClient.submit_option_order -- see that docstring.
        Callers must have already run this through a risk gate; this method
        performs no pricing, clamping, or sizing of its own, and carries no
        execute switch itself (that gate belongs one layer up, in the options
        engine/service, matching alpaca_live.py's own division of concerns)."""
        from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

        if not legs:
            raise ValueError("submit_option_order requires at least one leg")
        if limit_price <= 0:
            raise ValueError(f"limit_price must be positive, got {limit_price!r}")

        _INTENT = {
            "buy_to_open": PositionIntent.BUY_TO_OPEN,
            "buy_to_close": PositionIntent.BUY_TO_CLOSE,
            "sell_to_open": PositionIntent.SELL_TO_OPEN,
            "sell_to_close": PositionIntent.SELL_TO_CLOSE,
        }
        sdk_legs = []
        for leg in legs:
            intent = _INTENT.get((leg.position_intent or "").lower())
            if intent is None:
                intent = PositionIntent.BUY_TO_OPEN if leg.side.lower() == "buy" else PositionIntent.SELL_TO_OPEN
            sdk_legs.append(
                OptionLegRequest(
                    symbol=leg.symbol,
                    ratio_qty=leg.ratio_qty,
                    side=OrderSide.BUY if leg.side.lower() == "buy" else OrderSide.SELL,
                    position_intent=intent,
                )
            )

        order_class = OrderClass.MLEG if len(sdk_legs) >= 2 else OrderClass.SIMPLE
        request_kwargs: Dict[str, Any] = dict(
            qty=qty,
            order_class=order_class,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
        )
        if order_class == OrderClass.MLEG:
            request_kwargs["legs"] = sdk_legs
        else:
            leg = sdk_legs[0]
            request_kwargs["symbol"] = leg.symbol
            request_kwargs["side"] = leg.side

        try:
            order = self._trading.submit_order(order_data=LimitOrderRequest(**request_kwargs))
        except Exception as e:
            print(f"Exception in AlpacaLiveOptionsClient.submit_option_order: {e}")
            return None

        return OptionOrderResult(
            order_id=str(order.id) if order.id else None,
            status=str(order.status) if order.status else None,
            legs=[leg.symbol for leg in legs],
            raw={"id": str(order.id), "status": str(order.status)},
        )

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._trading.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            print(f"Exception in AlpacaLiveOptionsClient.cancel_order: {e}")
            return False
