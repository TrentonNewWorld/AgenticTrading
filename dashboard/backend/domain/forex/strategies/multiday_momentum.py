"""20-Day Momentum Rank for the forex dashboard. Hold the top 2 symbols by trailing 20-day return, re-ranked daily.

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

_CODE = '\ndef decide_forex(as_of, positions, quotes, account):\n    def _sma(xs, n):\n        return sum(xs[-n:]) / n if len(xs) >= n else None\n\n    def _rsi(xs, n=14):\n        if len(xs) < n + 1:\n            return None\n        gains, losses = 0.0, 0.0\n        for i in range(len(xs) - n, len(xs)):\n            d = xs[i] - xs[i - 1]\n            if d > 0: gains += d\n            else: losses -= d\n        if losses == 0:\n            return 100.0\n        rs = (gains / n) / (losses / n)\n        return 100.0 - 100.0 / (1.0 + rs)\n\n    def _stdev(xs, n):\n        if len(xs) < n:\n            return None\n        w = xs[-n:]\n        m = sum(w) / n\n        return (sum((x - m) ** 2 for x in w) / n) ** 0.5\n    held = {p["symbol"]: p["qty"] for p in positions}\n    scores = {}\n    for symbol, q in quotes.items():\n        closes = q.get("closes") or []\n        price = q.get("price")\n        if price is None or price <= 0 or len(closes) < 21:\n            continue\n        scores[symbol] = closes[-1] / closes[-21] - 1.0\n    ranked = sorted(scores, key=lambda s: scores[s], reverse=True)\n    want = set(ranked[:2]) if ranked else set()\n    intents = []\n    for symbol, qty_held in held.items():\n        if symbol not in want:\n            intents.append({"action": "close", "symbol": symbol, "side": "sell", "qty": qty_held})\n    for symbol in want:\n        if symbol in held:\n            continue\n        q = quotes.get(symbol) or {}\n        price = q.get("price")\n        if not price or price <= 0 or scores.get(symbol, 0) <= 0:\n            continue\n        qty = 500\n        intents.append({"action": "open", "symbol": symbol, "side": "buy", "qty": qty})\n    return intents\n'


class MultidayMomentumStrategy(ForexBaselineStrategy):
    key = "fx_multiday_momentum"

    def required_symbols(self) -> List[str]:
        return list(FOREX_UNIVERSE)

    def code(self) -> str:
        return _CODE
