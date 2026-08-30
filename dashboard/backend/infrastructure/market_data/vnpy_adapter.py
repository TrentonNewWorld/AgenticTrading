"""Convert vn.py bar objects into NewWorldTrading's normalized OHLCV schema."""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite

import pandas as pd
from vnpy.trader.object import BarData


OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
MARKET_TIMEZONE = "US/Eastern"


class InvalidVnpyBarData(ValueError):
    """Raised when vn.py bars violate the normalized market-data contract."""


def _context(bar: BarData) -> str:
    return f"symbol={bar.symbol!r}, timestamp={bar.datetime!r}"


def _number(bar: BarData, field: str) -> float:
    try:
        value = float(getattr(bar, field))
    except (TypeError, ValueError) as exc:
        raise InvalidVnpyBarData(
            f"Invalid {field} for {_context(bar)}: expected a number"
        ) from exc
    return value


def _validated_row(bar: BarData) -> tuple[pd.Timestamp, dict[str, float]]:
    dt = bar.datetime
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise InvalidVnpyBarData(
            f"Invalid timestamp for {_context(bar)}: expected timezone-aware datetime"
        )

    timestamp = pd.Timestamp(dt).tz_convert(MARKET_TIMEZONE)
    open_price = _number(bar, "open_price")
    high_price = _number(bar, "high_price")
    low_price = _number(bar, "low_price")
    close_price = _number(bar, "close_price")
    volume = _number(bar, "volume")

    prices = {
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
    }
    for field, value in prices.items():
        if not isfinite(value) or value <= 0:
            raise InvalidVnpyBarData(
                f"Invalid {field} price for {_context(bar)}: {value!r}"
            )

    if high_price < max(open_price, close_price):
        raise InvalidVnpyBarData(
            f"Invalid high price for {_context(bar)}: high must cover open and close"
        )
    if low_price > min(open_price, close_price):
        raise InvalidVnpyBarData(
            f"Invalid low price for {_context(bar)}: low must cover open and close"
        )
    if not isfinite(volume) or volume < 0:
        raise InvalidVnpyBarData(
            f"Invalid volume for {_context(bar)}: {volume!r}"
        )

    return timestamp, {**prices, "volume": volume}


def bars_to_frame(bars: Sequence[BarData]) -> pd.DataFrame:
    """Convert one symbol's vn.py bars into a validated, sorted OHLCV frame."""
    if not bars:
        frame = pd.DataFrame(columns=OHLCV_COLUMNS, dtype=float)
        frame.index = pd.DatetimeIndex([], name="timestamp", tz=MARKET_TIMEZONE)
        return frame

    symbols = {bar.symbol for bar in bars}
    if len(symbols) != 1:
        raise InvalidVnpyBarData(
            f"Invalid vn.py bar batch: mixed symbols {sorted(symbols)!r}"
        )

    rows: list[dict[str, float]] = []
    timestamps: list[pd.Timestamp] = []
    seen: set[pd.Timestamp] = set()
    for bar in bars:
        timestamp, row = _validated_row(bar)
        if timestamp in seen:
            raise InvalidVnpyBarData(
                f"Invalid vn.py bar batch: duplicate timestamp {timestamp.isoformat()} "
                f"for symbol={bar.symbol!r}"
            )
        seen.add(timestamp)
        timestamps.append(timestamp)
        rows.append(row)

    frame = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps, name="timestamp"))
    return frame.loc[:, OHLCV_COLUMNS].sort_index()
