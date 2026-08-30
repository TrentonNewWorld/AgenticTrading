"""Verified-live fee formulas (see domain/prediction/fees.py's docstring for
the web-research source): fee = ceil_cent(rate * qty * price * (1 - price)),
peaking at a 50c price.
"""

from __future__ import annotations

import pytest

from dashboard.backend.domain.prediction.fees import fee_for, kalshi_fee, polymarket_fee


def test_kalshi_fee_peaks_at_fifty_cents():
    at_50 = kalshi_fee(100, 0.50)
    at_10 = kalshi_fee(100, 0.10)
    at_90 = kalshi_fee(100, 0.90)
    assert at_50 > at_10
    assert at_50 > at_90


def test_kalshi_fee_symmetric_around_fifty_cents():
    assert kalshi_fee(100, 0.30) == pytest.approx(kalshi_fee(100, 0.70))


def test_kalshi_fee_matches_hand_computed_value():
    # 0.07 * 100 * 0.5 * 0.5 = 1.75 -> ceil to cent = 1.75
    assert kalshi_fee(100, 0.5) == pytest.approx(1.75)


def test_polymarket_fee_matches_hand_computed_value():
    # 0.05 * 100 * 0.5 * 0.5 = 1.25
    assert polymarket_fee(100, 0.5) == pytest.approx(1.25)


def test_zero_or_negative_qty_is_free():
    assert kalshi_fee(0, 0.5) == 0.0
    assert kalshi_fee(-5, 0.5) == 0.0


def test_out_of_range_price_is_free():
    assert kalshi_fee(100, 1.5) == 0.0
    assert kalshi_fee(100, -0.1) == 0.0


def test_fee_for_dispatches_by_platform():
    assert fee_for("kalshi", 100, 0.5) == kalshi_fee(100, 0.5)
    assert fee_for("polymarket", 100, 0.5) == polymarket_fee(100, 0.5)
    with pytest.raises(ValueError):
        fee_for("robinhood", 100, 0.5)
