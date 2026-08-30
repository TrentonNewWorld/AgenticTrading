"""Options Strategy Catalog: every strategy in the Options dashboard's own
roster (dashboard/config/leaderboard_options.json), with a description, an
equity-curve chart, and Reports/Export.

Sub-phase 8 of the Options-dashboard plan. Sibling router to
``api/routers/strategy_catalog.py`` (the stocks catalog) -- new URL space
(``/v1/options/strategy-catalog``) rather than parameterizing the existing
router, since the underlying ``domain.options.catalog`` module is already a
full sibling of ``domain.leaderboard.catalog``, not a variant of it.

Run in Paper/Run in Live buttons are not included in this phase -- the
stocks catalog's versions go through a dedicated risk-gated execution
service (``execution/alpaca_paper_service.py``/``alpaca_live_service.py``)
this phase does not build an Options equivalent of; Reports/Export/browse
are what Sub-phase 8 scopes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from dashboard.backend.domain.options.catalog import (
    build_export,
    generate_report,
    get_strategy_catalog,
    remove_strategy,
)

router = APIRouter(prefix="/v1/options/strategy-catalog", tags=["options-strategy-catalog"])


@router.get("")
def list_catalog(refresh: bool = False):
    return get_strategy_catalog(force_refresh=refresh)


@router.delete("/{key}")
def delete_catalog_strategy(key: str):
    removed = remove_strategy(key)
    if not removed:
        raise HTTPException(status_code=404, detail=f"no strategy '{key}' in the options catalog")
    return {"removed": key}


@router.get("/{key}/report")
def get_strategy_report(key: str):
    """Month-by-month + overall P&L, always at a $1,000 starting wallet over
    the most recent completed year -- the "Reports" button's data."""
    try:
        return generate_report(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/{key}/export")
def export_strategy(key: str):
    try:
        package = build_export(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    filename = f"{(package.get('name') or key).strip().lower().replace(' ', '_')}.strategy.json"
    return JSONResponse(content=package, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
