"""Real per-fill fee formulas for Kalshi and Polymarket -- verified live via
web research this session (2026-08-24), not assumed. Both platforms use the
same mathematical shape (peaks at a 50c price, shrinks toward 0/1): a
strategy trading near a coin-flip costs more per contract than one trading a
near-lock or a longshot.

Kalshi taker fee (effective schedule as of this session):
    fee = ceil_cent(0.07 * contracts * price * (1 - price))

Polymarket taker fee varies by market category (politics/finance ~0.04,
sports ~0.03, crypto ~0.07, geopolitics fee-free); this module uses a single
blended default (0.05, matching Polymarket's own US-exchange uniform rate)
rather than modeling every category split, since Prediction's market universe
isn't category-scoped yet:
    fee = ceil_cent(0.05 * shares * price * (1 - price))

Every dashboard besides Prediction assumes zero-fee fills -- this is the one
asset class where that assumption would be wrong (see CLAUDE.md's Prediction
notes: fees are part of why this dashboard forces a real forward paper-test
instead of an instant backtest).
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

KALSHI_TAKER_FEE_RATE = "0.07"
POLYMARKET_TAKER_FEE_RATE = "0.05"  # blended default -- see module docstring

_CENT = Decimal("0.01")


def _to_decimal(value: float) -> Decimal:
    """``value`` as the Decimal matching its *displayed* precision, not its
    exact binary float value. ``Decimal(0.5)`` is exactly representable and
    fine either way, but a caller-supplied price like ``0.3`` is NOT exactly
    representable as a float -- ``Decimal(0.3)`` would import its true binary
    value (0.299999999999999988...), not the "0.3" the caller meant.
    ``Decimal(str(value))`` (or ``repr``, equivalent for a float) takes the
    shortest decimal string that round-trips to that float, which is what
    the caller actually typed/meant."""
    return Decimal(str(value))


def _fee(rate: str, qty: float, price: float) -> float:
    """The whole fee formula computed in Decimal end to end -- not just its
    final rounding step. Multiplying floats first and only converting to
    Decimal for the last ceil-to-cent step still lets binary float error
    accumulate through the multiplication chain, which previously turned an
    exact $1.75 fee into a spuriously overcharged $1.76 (found via this
    module's own regression tests, not a live account -- fixed before it
    shipped)."""
    if qty <= 0 or not (0.0 <= price <= 1.0):
        return 0.0
    q = _to_decimal(qty)
    p = _to_decimal(price)
    fee = Decimal(rate) * q * p * (Decimal(1) - p)
    return float(fee.quantize(_CENT, rounding=ROUND_CEILING))


def kalshi_fee(contracts: float, price: float) -> float:
    """Dollar fee for a Kalshi fill. ``price`` is 0-1 (a contract settles at
    $1 or $0)."""
    return _fee(KALSHI_TAKER_FEE_RATE, contracts, price)


def polymarket_fee(shares: float, price: float) -> float:
    """Dollar fee for a Polymarket fill. ``price`` is 0-1 (an outcome share
    settles at $1 or $0)."""
    return _fee(POLYMARKET_TAKER_FEE_RATE, shares, price)


def fee_for(platform: str, qty: float, price: float) -> float:
    if platform == "kalshi":
        return kalshi_fee(qty, price)
    if platform == "polymarket":
        return polymarket_fee(qty, price)
    raise ValueError(f"unknown platform {platform!r}")
