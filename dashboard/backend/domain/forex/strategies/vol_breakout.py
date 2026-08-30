"""Volatility Breakout for the forex dashboard. Enter when price jumps more than 1.5 twenty-day standard deviations above yesterday's close; exit below SMA(10).

Reads the trailing-close history the backtester/engine attach as
quotes[sym]["closes"] (added 2026-08-29); with too little history it simply
does nothing, so early window days and degraded live ticks fail safe.
Position sizing: 500 units per position (matches the existing basket strategies). Generated alongside its crypto and futures twins --
the three dashboards deliberately do not share strategy code (see
strategies/__init__.py), so drift is prevented by generation, not imports.
"""

from __future__ import annotations

from typing import List

from dashboard.backend.domain.forex.strategies.base import ForexBaselineStrategy
from dashboard.backend.infrastructure.market_data.yahoo_forex import FOREX_UNIVERSE

_CODE = 'def _sma(xs, n):\n    return sum(xs[-n:]) / n if len(xs) >= n else None\n\ndef _rsi(xs, n=14):\n    if len(xs) < n + 1:\n        return None\n    gains, losses = 0.0, 0.0\n    for i in range(len(xs) - n, len(xs)):\n        d = xs[i] - xs[i - 1]\n        if d > 0: gains += d\n        else: losses -= d\n    if losses == 0:\n        return 100.0\n    rs = (gains / n) / (losses / n)\n    return 100.0 - 100.0 / (1.0 + rs)\n\ndef _stdev(xs, n):\n    if len(xs) < n:\n        return None\n    w = xs[-n:]\n    m = sum(w) / n\n    return (sum((x - m) ** 2 for x in w) / n) ** 0.5\n\ndef decide_forex(as_of, positions, quotes, account):\n    held = {p["symbol"]: p["qty"] for p in positions}\n    intents = []\n    for symbol, q in quotes.items():\n        closes = q.get("closes") or []\n        price = q.get("price")\n        if price is None or price <= 0:\n            continue\n\n        prev = q.get("prev_close")\n        sd = _stdev(closes, 20)\n        s10 = _sma(closes, 10)\n        if prev is None or sd is None or s10 is None:\n            continue\n        if symbol in held and price < s10:\n            intents.append({"action": "close", "symbol": symbol, "side": "sell", "qty": held[symbol]})\n        elif symbol not in held and price > prev + 1.5 * sd:\n            qty = 500\n            intents.append({"action": "open", "symbol": symbol, "side": "buy", "qty": qty})\n    return intents\n'


class VolBreakoutStrategy(ForexBaselineStrategy):
    key = "fx_vol_breakout"

    def required_symbols(self) -> List[str]:
        return list(FOREX_UNIVERSE)

    def code(self) -> str:
        return _CODE
