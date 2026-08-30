"""Buy-the-dip mean reversion across the same USD-quote pairs basket
momentum_basket.py trades -- the opposite side of the same one-day signal
(see that module's docstring). A pair that fell more than 0.5% from its
previous close is bought, expecting a bounce; closed once it recovers back
above that previous close. The threshold is smaller than
domain/futures/strategies/dip_reversion.py's 1% -- major FX pairs move in
much smaller daily percentages than futures, so a 1% dip is a rare, large
move for these pairs rather than an ordinary one.
"""

from __future__ import annotations

from typing import List

from dashboard.backend.domain.forex.strategies.base import ForexBaselineStrategy
from dashboard.backend.infrastructure.market_data.yahoo_forex import FOREX_UNIVERSE

_CODE = '''
def decide_forex(as_of, positions, quotes, account):
    held = {p["symbol"] for p in positions}
    intents = []
    for symbol, q in quotes.items():
        price = q.get("price")
        prev_close = q.get("prev_close")
        if price is None or prev_close is None or prev_close <= 0:
            continue
        change_pct = (price - prev_close) / prev_close * 100
        if symbol in held and price >= prev_close:
            intents.append({"action": "close", "symbol": symbol, "side": "sell", "qty": 500})
        elif symbol not in held and change_pct <= -0.5:
            intents.append({"action": "open", "symbol": symbol, "side": "buy", "qty": 500})
    return intents
'''


class DipReversionStrategy(ForexBaselineStrategy):
    key = "fx_dip_reversion"

    def required_symbols(self) -> List[str]:
        return list(FOREX_UNIVERSE)

    def code(self) -> str:
        return _CODE
