"""Shared Alpaca credential resolution: Connections store first, then each
broker client's own existing env-var/file fallback.

Sub-phase 3 of the Options-dashboard plan. Before this, ``domain.connections``
stored keys a user saved on the Connections page but nothing ever read them
back -- every broker call was env-var/file only, regardless of what a signed-in
user had configured. This module is the one seam both ``alpaca_paper.py`` and
``alpaca_live.py`` call through so a user's own saved key is preferred without
duplicating the Connections lookup in each.

Deliberately scoped to **request-scoped, user-initiated actions only** (e.g.
Strategy Catalog's Run in Paper/Live button, which already has a signed-in
user from the request). The ``manual10`` engine runs unattended on a
background scheduler thread with no logged-in user to resolve against, so it
never has a ``user_id`` to pass here and falls straight through to env/file
credentials, same as before this module existed -- that is an explicit scope
boundary, not an oversight.
"""

from __future__ import annotations

from typing import Literal, Optional, Tuple

AlpacaProvider = Literal["alpaca_paper", "alpaca_live", "alpaca_ira"]


def resolve_alpaca_credentials(
    user_id: Optional[int], provider: AlpacaProvider
) -> Optional[Tuple[str, str]]:
    """``(api_key, secret_key)`` from this user's saved Connections entry, or
    ``None`` if there's no user to resolve against (unattended context) or
    nothing saved -- callers fall through to their own env/file logic either
    way, so returning ``None`` is always safe."""
    if user_id is None:
        return None

    from dashboard.backend.domain.connections.repository import connection_store

    creds = connection_store.get_credentials(int(user_id), provider)
    if not creds:
        return None
    api_key = creds.get("api_key")
    secret_key = creds.get("secret_key")
    if not api_key or not secret_key:
        return None
    return (api_key, secret_key)
