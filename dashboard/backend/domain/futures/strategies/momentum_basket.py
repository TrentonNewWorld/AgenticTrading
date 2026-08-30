"""Day-over-day momentum, across a diversified futures basket.

Like domain/options/strategies/long_call_momentum.py, this is simpler than
its name might suggest for a specific reason: the decide_futures() sandbox
contract (built to mirror decide_options()'s own stateless, no-history
design -- see domain/futures/sandbox.py's docstring) hands the strategy only
today's quote and yesterday's close, never a price series. A real N-day
moving-average crossover needs history this contract doesn't provide. This
strategy instead trades the one signal actually available every cycle:
whether each symbol closed above or below its own previous close.
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
        if price is None or prev_close is None:
            continue
        is_up = price > prev_close
        if symbol in held and not is_up:
            intents.append({"action": "close", "symbol": symbol, "side": "sell", "qty": 1})
        elif symbol not in held and is_up:
            intents.append({"action": "open", "symbol": symbol, "side": "buy", "qty": 1})
    return intents
'''


class MomentumBasketStrategy(FuturesBaselineStrategy):
    key = "fut_momentum_basket"

    def required_symbols(self) -> List[str]:
        return list(FUTURES_UNIVERSE)

    def code(self) -> str:
        return _CODE
