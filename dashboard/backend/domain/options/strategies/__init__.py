"""Options starter strategies -- Sub-phase 8 of the Options-dashboard plan.

Own registry, deliberately not merged into ``domain.leaderboard.strategies``:
the existing ~11 stock baseline strategies stay stock-only, and Options gets
its own from-scratch roster (the user's explicit decision -- strategies do
not carry over between dashboards).
"""

from __future__ import annotations

from .registry import available_strategies, get_strategy

__all__ = ["available_strategies", "get_strategy"]
