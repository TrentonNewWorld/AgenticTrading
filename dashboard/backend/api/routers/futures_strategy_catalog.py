"""Futures Strategy Catalog HTTP surface. Mirrors
api/routers/options_strategy_catalog.py exactly -- same reasoning: sibling
router, no Run in Paper/Live in this phase.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from dashboard.backend.domain.futures.catalog import (
    build_export,
    generate_report,
    get_strategy_catalog,
    remove_strategy,
)

router = APIRouter(prefix="/v1/futures/strategy-catalog", tags=["futures-strategy-catalog"])


@router.get("")
def list_catalog(refresh: bool = False):
    return get_strategy_catalog(force_refresh=refresh)


@router.delete("/{key}")
def delete_catalog_strategy(key: str):
    removed = remove_strategy(key)
    if not removed:
        raise HTTPException(status_code=404, detail=f"no strategy '{key}' in the futures catalog")
    return {"removed": key}


@router.get("/{key}/report")
def get_strategy_report(key: str):
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
