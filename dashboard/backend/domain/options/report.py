"""Month-by-month + overall P&L report for an options equity curve.

Sub-phase 6 of the Options-dashboard plan. The month-by-month/overall math
in ``strategy_testing/report.py``'s ``build_report`` is genuinely
asset-class-agnostic (it only ever looks at ``{date, equity}`` pairs) --
reused directly rather than duplicated.
"""

from __future__ import annotations

from typing import Any, Dict, List

from dashboard.backend.domain.strategy_testing.report import build_report as _build_report


def build_report(curve: List[Dict[str, Any]], initial_capital: float) -> Dict[str, Any]:
    return _build_report(curve, initial_capital)
