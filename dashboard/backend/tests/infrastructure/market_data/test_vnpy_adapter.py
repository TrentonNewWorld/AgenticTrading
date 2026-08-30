"""Conversion contract from real vn.py BarData to NewWorldTrading OHLCV.

These require the optional ``vnpy`` dependency (``requirements-vnpy.txt``); when
it is absent the whole module is skipped so the suite stays green on minimal
interpreters. Without the skip the module-level ``vnpy`` import raises during
*collection*, which aborts the entire pytest session rather than failing one
module.
"""

from __future__ import annotations

from datetime import datetime
from math import inf, nan
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

pytest.importorskip("vnpy")

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from dashboard.backend.infrastructure.market_data.vnpy_adapter import (
    InvalidVnpyBarData,
    bars_to_frame,
)


ET = ZoneInfo("US/Eastern")


def make_bar(
    hour: int,
    *,
    symbol: str = "AAPL",
    dt: datetime | None = None,
    open_price: float = 100,
    high_price: float = 103,
    low_price: float = 99,
    close_price: float = 102,
    volume: float = 1_000,
) -> BarData:
    return BarData(
        gateway_name="VNPY_SIM",
        symbol=symbol,
        exchange=Exchange.SMART,
        datetime=dt or datetime(2026, 4, 1, hour, tzinfo=ET),
        interval=Interval.HOUR,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
    )


def test_maps_real_vnpy_bar_fields_and_sorts_timestamps():
    frame = bars_to_frame([make_bar(11), make_bar(10)])

    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert frame.index.name == "timestamp"
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert str(frame.index.tz) == "US/Eastern"
    assert frame.index.is_monotonic_increasing
    assert frame.iloc[0].to_dict() == {
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "volume": 1_000.0,
    }


def test_empty_input_has_stable_schema():
    frame = bars_to_frame([])

    assert frame.empty
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert frame.index.name == "timestamp"
    assert isinstance(frame.index, pd.DatetimeIndex)


def test_rejects_mixed_symbols():
    with pytest.raises(InvalidVnpyBarData, match="mixed symbols"):
        bars_to_frame([make_bar(10, symbol="AAPL"), make_bar(11, symbol="MSFT")])


def test_rejects_naive_timestamp():
    naive = datetime(2026, 4, 1, 10)

    with pytest.raises(InvalidVnpyBarData, match="timezone-aware"):
        bars_to_frame([make_bar(10, dt=naive)])


def test_rejects_duplicate_timestamp():
    with pytest.raises(InvalidVnpyBarData, match="duplicate timestamp"):
        bars_to_frame([make_bar(10), make_bar(10)])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open_price", 0),
        ("open_price", -1),
        ("high_price", nan),
        ("low_price", inf),
        ("close_price", -inf),
    ],
)
def test_rejects_non_finite_or_non_positive_prices(field, value):
    kwargs = {field: value}

    with pytest.raises(InvalidVnpyBarData, match="price"):
        bars_to_frame([make_bar(10, **kwargs)])


def test_rejects_high_below_open_or_close():
    with pytest.raises(InvalidVnpyBarData, match="high"):
        bars_to_frame([make_bar(10, open_price=100, high_price=101, close_price=102)])


def test_rejects_low_above_open_or_close():
    with pytest.raises(InvalidVnpyBarData, match="low"):
        bars_to_frame([make_bar(10, open_price=100, low_price=101, close_price=102)])


@pytest.mark.parametrize("volume", [-1, nan, inf])
def test_rejects_negative_or_non_finite_volume(volume):
    with pytest.raises(InvalidVnpyBarData, match="volume"):
        bars_to_frame([make_bar(10, volume=volume)])
