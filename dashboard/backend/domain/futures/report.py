"""Month-by-month + overall P&L report for a futures equity curve. The
month-by-month/overall math in strategy_testing/report.py's build_report is
asset-class-agnostic (it only looks at {date, equity} pairs) -- reused
directly rather than duplicated, matching domain/options/report.py.
"""

from __future__ import annotations

from typing import Any, Dict, List

from dashboard.backend.domain.strategy_testing.report import build_report as _build_report


def build_report(curve: List[Dict[str, Any]], initial_capital: float) -> Dict[str, Any]:
    return _build_report(curve, initial_capital)
