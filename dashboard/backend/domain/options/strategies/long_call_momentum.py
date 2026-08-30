"""Long call, single-leg directional reference strategy.

Sub-phase 8 starter #3 of the Options-dashboard plan. Originally scoped in
planning as a "20-day underlying momentum" signal deciding when to buy, but
the actual decide_options(as_of, positions, chain, account) sandbox contract
(built in Sub-phase 5, before this strategy was written) deliberately
carries no historical price series -- only the current day's chain/positions
snapshot, matching the stateless, no-network, no-persistent-state sandbox
model shared with every uploaded strategy. A real rolling momentum
calculation needs history this contract doesn't provide, and extending the
contract to add it would break the already-built-and-tested sandbox/engine/
backtester signature used throughout Sub-phases 5-7.

Kept as the simplest possible reference implementation instead: a single
long call at a near-the-money strike (chosen from the middle of the
available strike grid), held once until expiration, then a fresh entry --
still useful as the clearest example of a single-leg, directional (not
income/multi-leg) options strategy for anyone writing their own upload.
"""

from __future__ import annotations

from typing import List

from dashboard.backend.domain.options.strategies.base import OptionsBaselineStrategy

UNDERLYING = "SPY"

_CODE = '''
def decide_options(as_of, positions, chain, account):
    if positions:
        return []
    calls = sorted((c for c in chain.get("SPY", []) if c["right"] == "C"), key=lambda c: c["strike"])
    if not calls:
        return []
    idx = len(calls) // 2
    call = calls[idx]
    return [{"action": "open", "symbol": call["symbol"], "side": "buy", "qty": 1, "leg_role": "single"}]
'''


class LongCallMomentumStrategy(OptionsBaselineStrategy):
    key = "opt_long_call_momentum"

    def required_underlyings(self) -> List[str]:
        return [UNDERLYING]

    def code(self) -> str:
        return _CODE
