"""RSI-14 Reversion for the futures dashboard. Buy oversold (RSI14 < 30), exit on recovery (RSI14 > 55). The classic oscillator play.

Reads the trailing-close history the backtester/engine attach as
quotes[sym]["closes"] (added 2026-08-29); with too little history it simply
does nothing, so early window days and degraded live ticks fail safe.
Position sizing: 1 contract per position (matches the existing basket strategies). Generated alongside its crypto and forex twins --
the three dashboards deliberately do not share strategy code (see
strategies/__init__.py), so drift is prevented by generation, not imports.
"""

from __future__ import annotations

from typing import List

from dashboard.backend.domain.futures.strategies.base import FuturesBaselineStrategy
from dashboard.backend.infrastructure.market_data.yahoo_futures import FUTURES_UNIVERSE

_CODE = 'def _sma(xs, n):\n    return sum(xs[-n:]) / n if len(xs) >= n else None\n\ndef _rsi(xs, n=14):\n    if len(xs) < n + 1:\n        return None\n    gains, losses = 0.0, 0.0\n    for i in range(len(xs) - n, len(xs)):\n        d = xs[i] - xs[i - 1]\n        if d > 0: gains += d\n        else: losses -= d\n    if losses == 0:\n        return 100.0\n    rs = (gains / n) / (losses / n)\n    return 100.0 - 100.0 / (1.0 + rs)\n\ndef _stdev(xs, n):\n    if len(xs) < n:\n        return None\n    w = xs[-n:]\n    m = sum(w) / n\n    return (sum((x - m) ** 2 for x in w) / n) ** 0.5\n\ndef decide_futures(as_of, positions, quotes, account):\n    held = {p["symbol"]: p["qty"] for p in positions}\n    intents = []\n    for symbol, q in quotes.items():\n        closes = q.get("closes") or []\n        price = q.get("price")\n        if price is None or price <= 0:\n            continue\n\n        r = _rsi(closes, 14)\n        if r is None:\n            continue\n        if symbol in held and r > 55:\n            intents.append({"action": "close", "symbol": symbol, "side": "sell", "qty": held[symbol]})\n        elif symbol not in held and r < 30:\n            qty = 1\n            intents.append({"action": "open", "symbol": symbol, "side": "buy", "qty": qty})\n    return intents\n'


class RsiReversionStrategy(FuturesBaselineStrategy):
    key = "fut_rsi_reversion"

    def required_symbols(self) -> List[str]:
        return list(FUTURES_UNIVERSE)

    def code(self) -> str:
        return _CODE
