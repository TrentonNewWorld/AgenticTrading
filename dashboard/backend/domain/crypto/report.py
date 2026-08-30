"""Month-by-month + overall P&L report for a crypto equity curve. Mirrors
domain/futures/report.py and domain/forex/report.py exactly.
"""

from __future__ import annotations

from typing import Any, Dict, List

from dashboard.backend.domain.strategy_testing.report import build_report as _build_report


def build_report(curve: List[Dict[str, Any]], initial_capital: float) -> Dict[str, Any]:
    return _build_report(curve, initial_capital)
