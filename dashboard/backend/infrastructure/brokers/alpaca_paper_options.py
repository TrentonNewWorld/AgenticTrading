"""Alpaca **options** paper-trading broker adapter.

Sub-phase 4 of the Options-dashboard plan. New sibling module, following this
repo's established convention exactly (dashboard/backend/infrastructure/
brokers/alpaca_paper.py is the equities paper client, alpaca_live.py is the
equities live client, this is the options paper client) -- not a shared
abstraction layered onto the equities clients, since options need genuinely
different request shapes (multi-leg orders, ``OrderClass.MLEG``,
``OptionLegRequest``) that the equities clients never touch.

Uses the typed alpaca-py SDK (``TradingClient``, ``OptionLegRequest``) rather
than raw ``requests`` calls the way ``alpaca_paper.py`` does -- multi-leg
orders need the typed Pydantic models to build correctly (confirmed against a
real paper account: see
dashboard/backend/scripts/spike_options_data_findings.md).

Options trading uses the **same** Alpaca account and API key/secret as
equities (options approval is an account-level flag, not a separate
credential) -- this client shares the Connections page's existing
``alpaca_paper`` provider entry rather than needing its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from dashboard.backend.infrastructure.brokers.credentials import resolve_alpaca_credentials
from dashboard.backend.paths import CREDENTIALS_DIR


@dataclass
class OptionLeg:
    symbol: str
    side: str  # "buy" | "sell"
    ratio_qty: int = 1
    # "buy_to_open" | "buy_to_close" | "sell_to_open" | "sell_to_close"
    position_intent: Optional[str] = None


@dataclass
class OptionOrderResult:
    order_id: Optional[str]
    status: Optional[str]
    legs: List[str]
    raw: Dict[str, Any]


class AlpacaPaperOptionsClient:
    """Interface to Alpaca's options paper-trading API."""

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
                "options_buying_power": float(getattr(account, "options_buying_power", 0) or 0),
                "options_approved_level": getattr(account, "options_approved_level", None),
            }
        except Exception as e:
            print(f"Exception in AlpacaPaperOptionsClient.get_account: {e}")
            return None

    def get_option_positions(self) -> List[Dict[str, Any]]:
        """Every open options position (equity positions, if any, are
        filtered out by symbol shape -- an OCC contract symbol is always
        longer than any equity ticker and ends in a digit)."""
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
            print(f"Exception in AlpacaPaperOptionsClient.get_option_positions: {e}")
            return []

    def submit_option_order(
        self, legs: List[OptionLeg], *, limit_price: float, qty: int = 1
    ) -> Optional[OptionOrderResult]:
        """Submit a single- or multi-leg options order as a **limit** order --
        never a market order.

        Deliberately limit-only: a market order class is time-gated to
        regular trading hours on Alpaca's options book (confirmed against a
        real paper account), while a limit order is accepted any time and
        matches how a real options trader would price a spread anyway (a
        market order on a multi-leg combo risks a much worse fill than a
        limit at the intended net debit/credit). Callers must have already
        run this order through a risk gate and computed ``limit_price`` from
        the chain's own bid/ask (e.g. the net mid of the legs) -- this method
        performs no pricing, clamping, or sizing of its own.
        """
        if limit_price <= 0:
            raise ValueError(f"limit_price must be positive, got {limit_price!r}")
        from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

        if not legs:
            raise ValueError("submit_option_order requires at least one leg")

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
                # Default intent from side alone when the caller didn't specify
                # open/close -- opening a new position is the common case.
                intent = PositionIntent.BUY_TO_OPEN if leg.side.lower() == "buy" else PositionIntent.SELL_TO_OPEN
            sdk_legs.append(
                OptionLegRequest(
                    symbol=leg.symbol,
                    ratio_qty=leg.ratio_qty,
                    side=OrderSide.BUY if leg.side.lower() == "buy" else OrderSide.SELL,
                    position_intent=intent,
                )
            )

        # A single leg still uses the plain "simple" order class -- MLEG
        # requires at least 2 legs (confirmed against a real paper account:
        # a 1-leg MLEG order is rejected client-side by the SDK's own
        # validation before it ever reaches Alpaca).
        order_class = OrderClass.MLEG if len(sdk_legs) >= 2 else OrderClass.SIMPLE
        request_kwargs: Dict[str, Any] = dict(
            qty=qty,
            order_class=order_class,
            time_in_force=TimeInForce.DAY,
        )
        request_kwargs["limit_price"] = round(limit_price, 2)
        if order_class == OrderClass.MLEG:
            request_kwargs["legs"] = sdk_legs
        else:
            leg = sdk_legs[0]
            request_kwargs["symbol"] = leg.symbol
            request_kwargs["side"] = leg.side

        try:
            order = self._trading.submit_order(order_data=LimitOrderRequest(**request_kwargs))
        except Exception as e:
            print(f"Exception in AlpacaPaperOptionsClient.submit_option_order: {e}")
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
            print(f"Exception in AlpacaPaperOptionsClient.cancel_order: {e}")
            return False
