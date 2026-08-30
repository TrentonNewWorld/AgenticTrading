"""Day-over-day momentum, across the USD-quote major pairs basket. Same
signal shape as domain/futures/strategies/momentum_basket.py, for the same
reason: decide_forex() is stateless (see sandbox.py's docstring), so the
only signal available every cycle is today's price vs. yesterday's close.

qty=500 units per position (worth roughly $585-$1,090 depending on the pair
at typical major-pair rates) -- large enough to be a meaningful position
against the $1,000 wallet, small enough that the cash-sufficiency check in
backtester.py/engine.py doesn't refuse every single trade outright.
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
        if price is None or prev_close is None:
            continue
        is_up = price > prev_close
        if symbol in held and not is_up:
            intents.append({"action": "close", "symbol": symbol, "side": "sell", "qty": 500})
        elif symbol not in held and is_up:
            intents.append({"action": "open", "symbol": symbol, "side": "buy", "qty": 500})
    return intents
'''


class MomentumBasketStrategy(ForexBaselineStrategy):
    key = "fx_momentum_basket"

    def required_symbols(self) -> List[str]:
        return list(FOREX_UNIVERSE)

    def code(self) -> str:
        return _CODE
