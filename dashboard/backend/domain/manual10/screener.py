"""Opening-range momentum screener: today's biggest $1-$99 gainers.

Alpaca's Market Movers screener endpoint (`ScreenerClient.get_market_movers`)
already ranks gainers by percent change for the whole tape -- there is no
existing asset-universe scanner anywhere else in this codebase (confirmed by
a full-repo grep before writing this), and re-deriving "top movers" from raw
bars across the whole market would mean pulling thousands of symbols' data
ourselves for no reason. `top=50` (rather than the smallest request that would
satisfy `top_n`) leaves enough of the tape to filter down to the $1-$99 band
and still have `top_n` left after the price filter -- the raw top gainers are
frequently sub-$1 or triple-digit-plus names that fall out of range entirely.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from alpaca.data.historical.screener import MarketMoversRequest, ScreenerClient

#: Deliberately wider than any reasonable top_n -- see module docstring.
_MOVERS_POOL_SIZE = 50


def _client() -> ScreenerClient:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY/ALPACA_SECRET_KEY must be set to run the screener")
    return ScreenerClient(api_key, secret_key)


def find_top_movers(
    *, top_n: int, price_min: float, price_max: float, client: Optional[ScreenerClient] = None,
) -> List[Dict[str, Any]]:
    """Today's `top_n` biggest gainers priced between `price_min` and
    `price_max`, ranked 1..top_n by percent change. Returns
    ``[{"symbol", "latest_price", "change_pct", "rank"}, ...]`` -- the shape
    ``repository.save_candidates`` expects (``open_price`` is left for the
    caller to fill in from its own quote of record, since the screener's
    ``price`` field is "current", not "at market open")."""
    screener = client or _client()
    response = screener.get_market_movers(MarketMoversRequest(top=_MOVERS_POOL_SIZE))
    gainers = response.gainers if hasattr(response, "gainers") else response.get("gainers", [])

    in_range = []
    for g in gainers:
        price = g.price if hasattr(g, "price") else g.get("price")
        if price is None or not (price_min <= price <= price_max):
            continue
        in_range.append({
            "symbol": (g.symbol if hasattr(g, "symbol") else g["symbol"]).upper(),
            "latest_price": float(price),
            "change_pct": float(g.percent_change if hasattr(g, "percent_change") else g["percent_change"]),
        })

    in_range.sort(key=lambda c: c["change_pct"], reverse=True)
    picked = in_range[:top_n]
    for i, c in enumerate(picked, start=1):
        c["rank"] = i
    return picked
