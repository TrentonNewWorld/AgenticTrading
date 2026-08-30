"""Day-over-day momentum, across the major-coin USD basket. Same signal
shape as domain/futures/strategies/momentum_basket.py, for the same reason:
decide_crypto() is stateless (see sandbox.py's docstring), so the only
signal available every cycle is today's price vs. yesterday's close.

Position sizing is dollar-based ($150 per position / price = qty), not a
fixed unit count -- unlike futures/forex, this universe spans BTC at
~$77,000 down to DOGE at ~$0.09, so a fixed qty like "500 units" would be a
$38.5M BTC order and a $45 DOGE order from the same line of code. Sizing by
a fixed dollar amount per position keeps every trade a comparable, sane
fraction of the $1,000 wallet regardless of which coin it's for.
"""

from __future__ import annotations

from typing import List

from dashboard.backend.domain.crypto.strategies.base import CryptoBaselineStrategy
from dashboard.backend.infrastructure.market_data.alpaca_crypto import CRYPTO_UNIVERSE

_CODE = '''
def decide_crypto(as_of, positions, quotes, account):
    held = {p["symbol"] for p in positions}
    intents = []
    for symbol, q in quotes.items():
        price = q.get("price")
        prev_close = q.get("prev_close")
        if price is None or prev_close is None or price <= 0:
            continue
        is_up = price > prev_close
        if symbol in held and not is_up:
            held_qty = next((p["qty"] for p in positions if p["symbol"] == symbol), 0)
            intents.append({"action": "close", "symbol": symbol, "side": "sell", "qty": held_qty})
        elif symbol not in held and is_up:
            qty = round(150.0 / price, 6)
            intents.append({"action": "open", "symbol": symbol, "side": "buy", "qty": qty})
    return intents
'''


class MomentumBasketStrategy(CryptoBaselineStrategy):
    key = "cx_momentum_basket"

    def required_symbols(self) -> List[str]:
        return list(CRYPTO_UNIVERSE)

    def code(self) -> str:
        return _CODE
