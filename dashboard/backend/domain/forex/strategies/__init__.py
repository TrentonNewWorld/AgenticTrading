"""Forex starter strategies -- own registry, not merged into
domain.leaderboard.strategies, domain.options.strategies, or
domain.futures.strategies (strategies do not carry over between
dashboards, the user's own decision).
"""

from __future__ import annotations

from .registry import available_strategies, get_strategy

__all__ = ["available_strategies", "get_strategy"]
