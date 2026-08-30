"""Buy-the-dip mean reversion across the same major-coin basket
momentum_basket.py trades -- the opposite side of the same one-day signal
(see that module's docstring for the reasoning, including why sizing is
dollar-based). Crypto is more volatile day-to-day than FX majors, so the
dip threshold here is larger than domain/forex/strategies/dip_reversion.py's
0.5% -- a 0.5% move is unremarkable noise for BTC or DOGE, not a real dip.
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
        if price is None or prev_close is None or prev_close <= 0:
            continue
        change_pct = (price - prev_close) / prev_close * 100
        if symbol in held and price >= prev_close:
            held_qty = next((p["qty"] for p in positions if p["symbol"] == symbol), 0)
            intents.append({"action": "close", "symbol": symbol, "side": "sell", "qty": held_qty})
        elif symbol not in held and change_pct <= -3.0:
            qty = round(150.0 / price, 6)
            intents.append({"action": "open", "symbol": symbol, "side": "buy", "qty": qty})
    return intents
'''


class DipReversionStrategy(CryptoBaselineStrategy):
    key = "cx_dip_reversion"

    def required_symbols(self) -> List[str]:
        return list(CRYPTO_UNIVERSE)

    def code(self) -> str:
        return _CODE
