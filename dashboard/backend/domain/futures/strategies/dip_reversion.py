"""Buy-the-dip mean reversion, across the same futures basket
momentum_basket.py trades -- the opposite side of the same one-day signal
(see that module's docstring for why the signal is limited to price vs.
prev_close): a symbol that fell more than a threshold from its previous
close is bought, expecting a bounce; it's closed once it recovers back above
that previous close.
"""

from __future__ import annotations

from typing import List

from dashboard.backend.domain.futures.strategies.base import FuturesBaselineStrategy
from dashboard.backend.infrastructure.market_data.yahoo_futures import FUTURES_UNIVERSE

_CODE = '''
def decide_futures(as_of, positions, quotes, account):
    held = {p["symbol"] for p in positions}
    intents = []
    for symbol, q in quotes.items():
        price = q.get("price")
        prev_close = q.get("prev_close")
        if price is None or prev_close is None or prev_close <= 0:
            continue
        change_pct = (price - prev_close) / prev_close * 100
        if symbol in held and price >= prev_close:
            intents.append({"action": "close", "symbol": symbol, "side": "sell", "qty": 1})
        elif symbol not in held and change_pct <= -1.0:
            intents.append({"action": "open", "symbol": symbol, "side": "buy", "qty": 1})
    return intents
'''


class DipReversionStrategy(FuturesBaselineStrategy):
    key = "fut_dip_reversion"

    def required_symbols(self) -> List[str]:
        return list(FUTURES_UNIVERSE)

    def code(self) -> str:
        return _CODE
