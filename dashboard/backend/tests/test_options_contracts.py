"""Sub-phase 4 of the Options-dashboard plan: OCC symbol synthesis/parsing
and the candidate-contract enumerator that stands in for a "list expired
contracts" API Alpaca doesn't have.
"""

from __future__ import annotations

import tempfile
from datetime import date

import pytest

from dashboard.backend.infrastructure.market_data.alpaca_options import (
    OptionSymbolError,
    parse_occ_symbol,
    synthesize_occ_symbol,
)


# ---------------------------------------------------------------------------
# OCC symbol synthesis / parsing
# ---------------------------------------------------------------------------

def test_synthesize_occ_symbol_matches_real_example():
    # AAPL, 2026-09-18, Call, $185.00 -- a real symbol seen in the live spike
    # (dashboard/backend/scripts/spike_options_data_findings.md).
    symbol = synthesize_occ_symbol("AAPL", date(2026, 9, 18), "C", 185.0)
    assert symbol == "AAPL260918C00185000"


def test_synthesize_occ_symbol_fractional_strike():
    symbol = synthesize_occ_symbol("SPY", date(2025, 8, 15), "P", 150.5)
    assert symbol == "SPY250815P00150500"


def test_parse_occ_symbol_round_trips():
    parsed = parse_occ_symbol("AAPL260918C00185000")
    assert parsed == {
        "underlying": "AAPL",
        "expiration": date(2026, 9, 18),
        "right": "C",
        "strike": 185.0,
    }
    assert synthesize_occ_symbol(
        parsed["underlying"], parsed["expiration"], parsed["right"], parsed["strike"]
    ) == "AAPL260918C00185000"


def test_parse_occ_symbol_rejects_equity_ticker():
    with pytest.raises(OptionSymbolError):
        parse_occ_symbol("AAPL")


def test_parse_occ_symbol_rejects_garbage():
    with pytest.raises(OptionSymbolError):
        parse_occ_symbol("not-a-symbol")


def test_synthesize_occ_symbol_rejects_bad_right():
    with pytest.raises(OptionSymbolError):
        synthesize_occ_symbol("AAPL", date(2026, 9, 18), "X", 185.0)


def test_synthesize_occ_symbol_rejects_negative_strike():
    with pytest.raises(OptionSymbolError):
        synthesize_occ_symbol("AAPL", date(2026, 9, 18), "C", -5.0)


# ---------------------------------------------------------------------------
# Candidate-contract enumerator
# ---------------------------------------------------------------------------

@pytest.fixture
def contracts_module(monkeypatch):
    import dashboard.backend.domain.options.contracts as contracts_module

    db_path = tempfile.mktemp(suffix=".db")
    monkeypatch.setattr(contracts_module, "DB_PATH", db_path)
    contracts_module._init_schema()
    return contracts_module


def test_third_friday_known_month():
    from dashboard.backend.domain.options.contracts import third_friday

    # September 2026: 1st is a Tuesday, so the 3rd Friday is the 18th.
    assert third_friday(2026, 9) == date(2026, 9, 18)


def test_monthly_expirations_covers_window_with_slack():
    from dashboard.backend.domain.options.contracts import monthly_expirations

    expirations = monthly_expirations(date(2026, 1, 1), date(2026, 3, 31))
    # Dec (slack), Jan, Feb, Mar, Apr (slack) 3rd Fridays.
    assert len(expirations) == 5
    assert expirations == sorted(expirations)


def test_strike_grid_centers_on_reference_price():
    from dashboard.backend.domain.options.contracts import strike_grid

    # $150 falls in the $100-250 tier -> $5 increments (matches real listed
    # strike spacing for that price range).
    grid = strike_grid(150.0, levels=2)
    assert grid == [140.0, 145.0, 150.0, 155.0, 160.0]


def test_strike_grid_uses_tight_increment_for_low_priced_names():
    from dashboard.backend.domain.options.contracts import strike_grid

    grid = strike_grid(20.0, levels=2)
    assert grid == [19.0, 19.5, 20.0, 20.5, 21.0]


def test_strike_grid_uses_wider_increment_for_high_priced_names():
    from dashboard.backend.domain.options.contracts import strike_grid

    grid = strike_grid(500.0, levels=2)
    assert grid == [480.0, 490.0, 500.0, 510.0, 520.0]


def test_strike_grid_empty_for_non_positive_price():
    from dashboard.backend.domain.options.contracts import strike_grid

    assert strike_grid(0) == []
    assert strike_grid(-10) == []


def test_find_candidate_contracts_returns_only_symbols_with_data(contracts_module, monkeypatch):
    """Stubbed bars: some synthesized candidates have data, most don't (the
    realistic case -- most guessed strikes were never real listed contracts).
    Only the ones with data should come back, and the cache should record
    both outcomes."""
    from dashboard.backend.infrastructure.market_data.alpaca_options import synthesize_occ_symbol

    start, end = date(2026, 1, 1), date(2026, 2, 28)
    january_expiration = contracts_module.third_friday(2026, 1)
    winner_symbol = synthesize_occ_symbol("AAPL", january_expiration, "C", 150.0)

    fake_closes = {date(2026, 1, 2): 150.0, date(2026, 2, 27): 152.0}
    monkeypatch.setattr(contracts_module, "_fetch_underlying_daily_closes", lambda *a, **k: fake_closes)

    class _FakeBars:
        def __init__(self, rows):
            self._rows = rows

        def __len__(self):
            return len(self._rows)

    def _fake_get_option_bars(symbols, start, end):
        # Only the exact-ATM January call "has data" -- every other
        # synthesized candidate is a guess that wasn't a real contract.
        return {winner_symbol: _FakeBars([1, 2, 3])} if winner_symbol in symbols else {}

    monkeypatch.setattr(contracts_module, "get_option_bars", _fake_get_option_bars)

    results = contracts_module.find_candidate_contracts("AAPL", start, end)

    assert len(results) == 1
    assert results[0].symbol == winner_symbol
    assert results[0].strike == 150.0
    assert results[0].right == "C"
    assert results[0].bar_count == 3

    # Cache recorded both the hit and a representative miss.
    hit = contracts_module._cache_get(winner_symbol)
    assert hit is not None and hit["has_data"] == 1


def test_find_candidate_contracts_second_call_uses_cache_not_a_reprobe(contracts_module, monkeypatch):
    start, end = date(2026, 1, 1), date(2026, 2, 28)
    fake_closes = {date(2026, 1, 2): 150.0}
    monkeypatch.setattr(contracts_module, "_fetch_underlying_daily_closes", lambda *a, **k: fake_closes)

    probe_calls = {"count": 0}

    class _FakeBars:
        def __init__(self, rows):
            self._rows = rows

        def __len__(self):
            return len(self._rows)

    def _fake_get_option_bars(symbols, start, end):
        probe_calls["count"] += 1
        winner = next((s for s in symbols if s.endswith("C00150000")), None)
        return {winner: _FakeBars([1])} if winner else {}

    monkeypatch.setattr(contracts_module, "get_option_bars", _fake_get_option_bars)

    first = contracts_module.find_candidate_contracts("AAPL", start, end)
    assert len(first) >= 1
    calls_after_first = probe_calls["count"]
    assert calls_after_first > 0

    second = contracts_module.find_candidate_contracts("AAPL", start, end)
    assert len(second) == len(first)
    # Every candidate symbol was already cached from the first call -- the
    # second call must not re-probe Alpaca at all.
    assert probe_calls["count"] == calls_after_first


def test_find_candidate_contracts_empty_when_no_underlying_data(contracts_module, monkeypatch):
    monkeypatch.setattr(contracts_module, "_fetch_underlying_daily_closes", lambda *a, **k: {})
    results = contracts_module.find_candidate_contracts(
        "AAPL", date(2026, 1, 1), date(2026, 2, 28)
    )
    assert results == []
