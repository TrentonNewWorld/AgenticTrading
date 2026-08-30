"""Admin-only toggle for the DB-backed *_EXECUTE overrides in
domain/execution_settings.py -- currently just ``alpaca_live_execute``, the
flag behind the "Live Trading" switch in the account menu.

Admin-gated (not just signed-in) on purpose: this is a single, server-wide
switch (execute_enabled() takes no user_id -- it always has been a
deployment-level flag, matching the env var it now overrides), so exposing
it to every signed-in user would let any account arm real, unattended
order placement for the whole deployment, including whatever other users'
strategies are currently activated. Fine for this repo's single-operator
local deployment; load-bearing the moment this is ever hosted for more than
one person.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dashboard.backend.api.auth import require_admin
from dashboard.backend.domain.execution_settings import ALPACA_LIVE_EXECUTE_KEY, get_status, set_override

router = APIRouter(prefix="/admin/execution-settings", tags=["admin"])


@router.get("")
def get_execution_settings(current_user: dict = Depends(require_admin)):
    from dashboard.backend.execution.alpaca_live_service import execute_enabled

    status = get_status(ALPACA_LIVE_EXECUTE_KEY)
    return {
        "alpaca_live_execute": {
            # effective is what execute_enabled() actually returns right now
            # (falls back to the env var when nobody's touched the switch);
            # override is the raw stored row, so the UI can show "using the
            # server's default" vs. "explicitly set" distinctly.
            "effective": execute_enabled(),
            "override": status["enabled"],
            "updated_at": status["updated_at"],
        }
    }


class SetExecuteBody(BaseModel):
    enabled: bool


@router.put("/alpaca-live-execute")
def set_alpaca_live_execute(body: SetExecuteBody, current_user: dict = Depends(require_admin)):
    set_override(ALPACA_LIVE_EXECUTE_KEY, body.enabled, user_id=current_user["id"])
    from dashboard.backend.execution.alpaca_live_service import execute_enabled

    return {"alpaca_live_execute": {"effective": execute_enabled(), "override": body.enabled}}
