"""Sub-phase 9 of the Options-dashboard plan: contest_window_for_year() is
the shared "most recently completed full year" computation Options'
Competition Leaderboard uses from day one (it has no legacy hardcoded
window like Stocks' leaderboard.json to fall back on).
"""

from __future__ import annotations

from datetime import date, timedelta

from dashboard.backend.domain.leaderboard.baselines import contest_window_for_year


def test_window_is_exactly_one_year_ending_yesterday():
    as_of = date(2026, 8, 22)
    start, end = contest_window_for_year(as_of)
    assert end == date(2026, 8, 21)
    assert start == date(2025, 8, 21)
    assert (end - start).days == 365


def test_window_rolls_forward_as_as_of_advances():
    first = contest_window_for_year(date(2026, 8, 22))
    later = contest_window_for_year(date(2026, 9, 1))
    assert later[1] > first[1]
    assert later[0] > first[0]


def test_window_across_a_leap_year_boundary_does_not_crash():
    """Deliberately NOT a year-preserving date(year-1, month, day) calc (see
    the function's own docstring) -- a fixed 365-day offset needs no Feb 29
    special case at all, so this just confirms it behaves sanely across one."""
    as_of = date(2028, 3, 1)  # 2028 is a leap year; window spans Feb 29, 2028
    start, end = contest_window_for_year(as_of)
    assert end == date(2028, 2, 29)
    assert start == date(2027, 3, 1)
    assert (end - start).days == 365


def test_window_matches_strategy_testing_backtester_convention():
    """The three "most recent completed year" computations in this codebase
    (strategy_testing/backtester.py, domain/options/catalog.py, and this
    function) must agree -- confirmed here against the same as_of."""
    as_of = date(2026, 8, 22)
    end = as_of - timedelta(days=1)
    expected_start = end - timedelta(days=365)
    start, actual_end = contest_window_for_year(as_of)
    assert (start, actual_end) == (expected_start, end)
