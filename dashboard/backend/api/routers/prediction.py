"""Prediction dashboard HTTP surface. Unlike every other asset class, there
is only one queue (domain/prediction/repository.py) feeding all three
creation paths -- Manual and Testing/Upload both land here directly; My
Agents lands here too, but through api/routers/backtests.py's
non-stocks-agent branch (asset_class == "prediction" enqueues instead of
backtesting -- see that router's docstring note near the branch), not a
route in this file.

Mutating routes do no trading/ticking themselves -- domain/prediction/
scheduler.py's daily tick is what actually advances a strategy. This router
only touches the queue table.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dashboard.backend.api.auth import get_current_user_optional
from dashboard.backend.domain.prediction import repository as repo
from dashboard.backend.domain.prediction.repository import WAITING_DAYS_REQUIRED
from dashboard.backend.domain.prediction.sandbox import PredictionStrategyCodeError, validate_code

router = APIRouter(prefix="/v1/prediction", tags=["prediction"])

#: Shown at the moment of submission on every creation path (Manual,
#: Testing/Upload -- the frontend shows the same text for My Agents,
#: sourced from this same constant via a matching copy in app.js, since that
#: submission doesn't round-trip through this router at all).
FIVE_DAY_NOTICE = (
    f"This strategy will paper-trade forward for {WAITING_DAYS_REQUIRED} real days, with fees, "
    "before its results are shown -- not an instant backtest like every other dashboard here. "
    "Prediction markets often move on real news between now and then, more than stocks or crypto "
    "typically do, so an instant historical backtest would overstate what to expect. You may lose "
    "money faster here, or decide you'd rather trade it yourself."
)


class ManualSubmitBody(BaseModel):
    name: str = Field(..., max_length=100)
    description: str = Field("", max_length=2000)
    code: str = Field(..., max_length=50_000)


def _submit_code(*, body: ManualSubmitBody, source_type: str, user_id: Optional[int]):
    code = body.code
    description = body.description
    if source_type == "upload":
        # Doesn't have to already be exact decide_prediction()-shaped code --
        # see domain/strategy_extraction.py's docstring. Manual (typed/pasted
        # directly) is validated as-written, no conversion attempt: a user
        # composing code by hand is presumed to want it run as written, not
        # silently rewritten.
        from dashboard.backend.domain.strategy_extraction import extract_strategy_code

        extraction = extract_strategy_code(code, "prediction", user_id=user_id)
        if extraction.code is None:
            row = repo.create_rejected(
                name=body.name, description=description, source_type=source_type,
                code=code, error=extraction.summary, user_id=user_id,
            )
            raise HTTPException(status_code=400, detail=extraction.summary)
        code = extraction.code
        if not extraction.was_already_valid:
            description = (description + f"\n\n[Converted on upload: {extraction.summary}]").strip()

    try:
        validate_code(code)
    except PredictionStrategyCodeError as exc:
        row = repo.create_rejected(
            name=body.name, description=description, source_type=source_type,
            code=code, error=str(exc), user_id=user_id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    review_notes = None
    if source_type == "upload":
        review_notes = _llm_risk_review(code, user_id=user_id)

    return repo.create(
        name=body.name, description=description, source_type=source_type,
        code=code, review_notes=review_notes, user_id=user_id,
    )


def _llm_risk_review(code: str, user_id: Optional[int] = None) -> str:
    """Same heuristic second opinion every other upload path in this repo
    gets -- see domain/manual10/uploads.py's docstring for why this is
    never the actual security control (validate_code's AST allowlist is).
    Unlike those paths, this never blocks or requires human approval before
    the code runs: the 5-day forward test *is* the review here."""
    from dashboard.backend.infrastructure.llm.backtest_harness import HAS_ANTHROPIC, extract_response_text
    from dashboard.backend.infrastructure.llm.providers import make_llm_client

    if not HAS_ANTHROPIC:
        return "No LLM available in this environment -- risk review was not performed. RISK: unknown"
    client = make_llm_client(user_id=user_id)
    if client is None:
        return "No LLM API key configured -- risk review was not performed. RISK: unknown"
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=(
                "You are a security reviewer for a Python prediction-market trading strategy upload. "
                "The code already passed a strict static-analysis allowlist (only math/statistics/json/"
                "datetime imports, no eval/exec/open/os/subprocess/socket, no dunder attribute access), "
                "and will run in an isolated subprocess with no credentials, no network, and a hard "
                "timeout. The function is decide_prediction(as_of, positions, markets, account), "
                "returning a list of order intents. Your job is a SECOND, independent look for anything "
                "suspicious a mechanical check might miss: obfuscated intent, unusual control flow that "
                "looks designed to evade review, or logic that doesn't look like a real trading strategy "
                "at all. Respond with a short plain-text assessment (2-4 sentences) and end with exactly "
                'one line: "RISK: low" or "RISK: medium" or "RISK: high".'
            ),
            messages=[{"role": "user", "content": f"Review this uploaded strategy code:\n\n```python\n{code}\n```"}],
        )
        return extract_response_text(response).strip()
    except Exception as exc:  # noqa: BLE001
        return f"Risk review call failed: {exc}. RISK: unknown"


@router.get("/notice")
def get_five_day_notice():
    return {"notice": FIVE_DAY_NOTICE, "days_required": WAITING_DAYS_REQUIRED}


@router.post("/strategies/manual")
def submit_manual(body: ManualSubmitBody, current_user: Optional[dict] = Depends(get_current_user_optional)):
    return _submit_code(body=body, source_type="manual", user_id=current_user["id"] if current_user else None)


@router.post("/strategies/upload")
def submit_upload(body: ManualSubmitBody, current_user: Optional[dict] = Depends(get_current_user_optional)):
    return _submit_code(body=body, source_type="upload", user_id=current_user["id"] if current_user else None)


@router.get("/strategies")
def list_strategies(current_user: Optional[dict] = Depends(get_current_user_optional)):
    user_id = current_user["id"] if current_user else None
    return {"strategies": repo.list_all(user_id=user_id)}


@router.get("/strategies/{strategy_id}")
def get_strategy(strategy_id: str):
    row = repo.get(strategy_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no prediction strategy '{strategy_id}'")
    return row


@router.post("/strategies/{strategy_id}/add")
def add_strategy(strategy_id: str):
    row = repo.get(strategy_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no prediction strategy '{strategy_id}'")
    if row["status"] != "ready":
        raise HTTPException(
            status_code=400,
            detail=f"strategy is {row['status']!r}, not ready -- day {row['day_count']} of {WAITING_DAYS_REQUIRED}",
        )
    repo.mark_added(strategy_id)
    return repo.get(strategy_id)


@router.post("/strategies/{strategy_id}/delete")
def delete_strategy(strategy_id: str):
    row = repo.get(strategy_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no prediction strategy '{strategy_id}'")
    if row["status"] in ("rejected", "error"):
        repo.delete_permanently(strategy_id)
        return {"status": "deleted", "id": strategy_id}
    repo.mark_deleted(strategy_id)
    return repo.get(strategy_id)
