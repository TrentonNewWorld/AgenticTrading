"""Cash-secured put: sell 1 moderately out-of-the-money put each cycle,
holding cash collateral (no separate collateral accounting in the
backtester's cash ledger -- the short put's mark-to-market liability already
reflects the obligation, matching how the engine treats every short leg).

Sub-phase 8 starter #2 of the Options-dashboard plan -- a single-leg,
income-style strategy (the mirror image of the covered call's short-call
leg, without the stock leg).
"""

from __future__ import annotations

from typing import List

from dashboard.backend.domain.options.strategies.base import OptionsBaselineStrategy

UNDERLYING = "SPY"

_CODE = '''
def decide_options(as_of, positions, chain, account):
    if positions:
        return []
    puts = sorted((c for c in chain.get("SPY", []) if c["right"] == "P"), key=lambda c: c["strike"])
    if not puts:
        return []
    idx = max(0, int(len(puts) / 3))
    put = puts[idx]
    return [{"action": "open", "symbol": put["symbol"], "side": "sell", "qty": 1, "leg_role": "single"}]
'''


class CashSecuredPutStrategy(OptionsBaselineStrategy):
    key = "opt_cash_secured_put"

    def required_underlyings(self) -> List[str]:
        return [UNDERLYING]

    def code(self) -> str:
        return _CODE
