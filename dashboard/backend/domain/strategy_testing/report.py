"""Month-by-month + overall P&L report from an equity curve.

Shared by the Testing page's "ready for review" result and the Strategy
Catalog's "Reports" button (both start from $1,000 and want the same shape:
one row per calendar month plus an overall summary) -- one function, two
callers, so the two surfaces can never quietly disagree on how a return
percentage or a drawdown is computed.
"""

from __future__ import annotations

from typing import Any, Dict, List


def build_report(curve: List[Dict[str, Any]], initial_capital: float) -> Dict[str, Any]:
    """``curve`` is [{"date": "YYYY-MM-DD", "equity": float}, ...], already in
    date order. Returns {overall: {...}, months: [{month, start, end,
    return_pct}, ...], curve}."""
    if not curve:
        return {
            "overall": {"final": initial_capital, "return_pct": 0.0, "max_drawdown_pct": 0.0},
            "months": [],
            "curve": [],
        }

    equity = [float(row["equity"]) for row in curve]
    final = equity[-1]
    overall_return_pct = (final / initial_capital - 1) * 100 if initial_capital else 0.0
    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak:
            max_dd = min(max_dd, (e - peak) / peak * 100)

    months: List[Dict[str, Any]] = []
    month_key = None
    month_start_equity = initial_capital
    prev_equity = initial_capital
    for row in curve:
        date = str(row["date"])
        key = date[:7]  # YYYY-MM
        if key != month_key:
            if month_key is not None:
                months.append({
                    "month": month_key,
                    "start": round(month_start_equity, 2),
                    "end": round(prev_equity, 2),
                    "return_pct": round((prev_equity / month_start_equity - 1) * 100, 2) if month_start_equity else 0.0,
                })
            month_key = key
            month_start_equity = prev_equity
        prev_equity = float(row["equity"])
    if month_key is not None:
        months.append({
            "month": month_key,
            "start": round(month_start_equity, 2),
            "end": round(prev_equity, 2),
            "return_pct": round((prev_equity / month_start_equity - 1) * 100, 2) if month_start_equity else 0.0,
        })

    return {
        "overall": {
            "starting_wallet": round(initial_capital, 2),
            "final": round(final, 2),
            "pnl": round(final - initial_capital, 2),
            "return_pct": round(overall_return_pct, 2),
            "max_drawdown_pct": round(max_dd, 2),
        },
        "months": months,
        "curve": [{"date": str(row["date"]), "equity": round(float(row["equity"]), 2)} for row in curve],
    }
