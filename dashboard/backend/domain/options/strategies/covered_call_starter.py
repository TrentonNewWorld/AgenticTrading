"""Covered call: long 100 shares of the underlying + short 1 moderately
out-of-the-money call, rolled after each expiration.

Sub-phase 8 starter #1 of the Options-dashboard plan -- the two-leg,
stock+option case (exercises the backtester's mixed-leg-type path added
alongside this roster).
"""

from __future__ import annotations

from typing import List

from dashboard.backend.domain.options.strategies.base import OptionsBaselineStrategy

UNDERLYING = "SPY"

#: The sandbox contract (decide_options(as_of, positions, chain, account))
#: deliberately carries no historical price series -- only today's chain/
#: positions snapshot, matching the stateless, no-network sandbox model
#: shared with uploaded strategies. There's no live spot price passed
#: separately either, so "moderately OTM" here is approximated from the
#: available strike grid's own shape (the top third of listed call strikes)
#: rather than a real delta/moneyness calculation.
_CODE = '''
def decide_options(as_of, positions, chain, account):
    intents = []
    stock_positions = [p for p in positions if p.get("leg_role") == "stock"]
    option_positions = [p for p in positions if p.get("leg_role") == "option"]

    if not stock_positions and "SPY" in chain:
        intents.append({"action": "open", "symbol": "SPY", "side": "buy", "qty": 100, "leg_role": "stock"})

    if stock_positions and not option_positions:
        calls = sorted((c for c in chain.get("SPY", []) if c["right"] == "C"), key=lambda c: c["strike"])
        if calls:
            idx = min(len(calls) - 1, int(len(calls) * 2 / 3))
            call = calls[idx]
            intents.append({
                "action": "open", "symbol": call["symbol"], "side": "sell",
                "qty": 1, "leg_role": "option",
            })
    return intents
'''


class CoveredCallStarterStrategy(OptionsBaselineStrategy):
    key = "opt_covered_call_starter"

    def required_underlyings(self) -> List[str]:
        return [UNDERLYING]

    def code(self) -> str:
        return _CODE
