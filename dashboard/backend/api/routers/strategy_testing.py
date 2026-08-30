"""Testing page: upload a strategy, watch it get scanned/backtested, decide.

Mutating routes do no scanning/backtesting themselves -- they only touch the
queue table (``domain/strategy_testing/repository.py``); the actual scan and
backtest happen on the background worker thread
(``domain/strategy_testing/worker.py``), started once at app startup, so an
HTTP request here never blocks on either.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from dashboard.backend.api.auth import get_current_user_optional
from dashboard.backend.domain.strategy_extraction import extract_strategy_code
from dashboard.backend.domain.strategy_testing import promote, repository as repo

router = APIRouter(prefix="/v1/strategy-testing", tags=["strategy-testing"])

MAX_CODE_CHARS = 50_000


_VALID_ASSET_CLASSES = {"stocks", "options", "futures", "forex", "crypto"}


@router.get("/queue")
def get_queue(asset_class: str = "stocks"):
    if asset_class not in _VALID_ASSET_CLASSES:
        raise HTTPException(status_code=400, detail=f"asset_class must be one of {sorted(_VALID_ASSET_CLASSES)}")
    return {"items": repo.list_all(asset_class)}


@router.post("/submit")
async def submit(
    file: UploadFile = File(...),
    name: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    asset_class: str = Form(default="stocks"),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    if asset_class not in _VALID_ASSET_CLASSES:
        raise HTTPException(status_code=400, detail=f"asset_class must be one of {sorted(_VALID_ASSET_CLASSES)}")
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="file must be UTF-8 text") from exc

    resolved_name = name
    resolved_description = description or ""
    code = text

    # Accept either the exported JSON package (name/description/code inline)
    # or a bare .py file with just a decide() function.
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and "code" in data:
            code = str(data.get("code") or "")
            resolved_name = resolved_name or data.get("name")
            resolved_description = resolved_description or data.get("description") or ""

    if not resolved_name:
        resolved_name = (file.filename or "Uploaded Strategy").rsplit(".", 1)[0]

    if not code.strip():
        raise HTTPException(status_code=400, detail="no code found in upload")
    if len(code) > MAX_CODE_CHARS:
        raise HTTPException(status_code=400, detail=f"code is over the {MAX_CODE_CHARS}-character limit")

    # Doesn't have to already be exact decide()-shaped code: an LLM attempts
    # to recognize/convert it before this ever reaches the queue, so a
    # strategy pasted from elsewhere or shaped for a different asset class
    # gets a real chance instead of an outright AST-allowlist rejection. The
    # converted output is still re-validated through that same allowlist
    # before being accepted -- see domain/strategy_extraction.py's docstring.
    user_id = current_user["id"] if current_user else None
    extraction = extract_strategy_code(code, asset_class, user_id=user_id)
    if extraction.code is None:
        raise HTTPException(status_code=400, detail=extraction.summary)
    code = extraction.code
    if not extraction.was_already_valid:
        resolved_description = (resolved_description + f"\n\n[Converted on upload: {extraction.summary}]").strip()

    row = repo.enqueue(
        name=resolved_name.strip(), description=resolved_description, code=code,
        source_filename=file.filename, asset_class=asset_class,
        user_id=user_id,
    )
    return row


@router.post("/{item_id}/delete")
def delete_item(item_id: str):
    row = repo.get(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    repo.delete_permanently(item_id)
    return {"status": "ok", "id": item_id}


@router.post("/{item_id}/add")
def add_item(item_id: str):
    row = repo.get_with_code(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"item is '{row['status']}', not ready to add")
    row_asset_class = row.get("asset_class")
    if row_asset_class == "options":
        # Lazy import: domain.options.catalog is a sibling module to this
        # router's stocks-only promote.py, kept separate per the plan's
        # decision that strategies never carry over between dashboards.
        from dashboard.backend.domain.options.catalog import add_to_catalog as add_options_to_catalog
        entry = add_options_to_catalog(name=row["name"], description=row["description"], code=row["code"])
    elif row_asset_class == "futures":
        from dashboard.backend.domain.futures.catalog import add_to_catalog as add_futures_to_catalog
        entry = add_futures_to_catalog(name=row["name"], description=row["description"], code=row["code"])
    elif row_asset_class == "forex":
        from dashboard.backend.domain.forex.catalog import add_to_catalog as add_forex_to_catalog
        entry = add_forex_to_catalog(name=row["name"], description=row["description"], code=row["code"])
    elif row_asset_class == "crypto":
        from dashboard.backend.domain.crypto.catalog import add_to_catalog as add_crypto_to_catalog
        entry = add_crypto_to_catalog(name=row["name"], description=row["description"], code=row["code"])
    else:
        entry = promote.add_to_catalog(name=row["name"], description=row["description"], code=row["code"])
    repo.mark_added(item_id)
    return {"status": "ok", "id": item_id, "catalog_entry": entry}


@router.get("/{item_id}/export")
def export_item(item_id: str):
    row = repo.get_with_code(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    package = promote.build_export_package(
        name=row["name"], description=row["description"], code=row["code"], source="testing_page",
    )
    filename = f"{row['name'].strip().lower().replace(' ', '_')}.strategy.json"
    return JSONResponse(
        content=package,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
