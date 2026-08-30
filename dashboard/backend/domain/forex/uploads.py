"""Forex Manual page uploads: submission and review workflow. Mirrors
domain/futures/uploads.py exactly.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from dashboard.backend.domain.forex import repository as repo
from dashboard.backend.domain.forex.sandbox import ForexStrategyCodeError, validate_code
from dashboard.backend.infrastructure.llm.backtest_harness import HAS_ANTHROPIC, extract_response_text, make_llm_client

MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 240

_RISK_REVIEW_SYSTEM_PROMPT = """You are a security reviewer for a Python FOREX trading strategy upload feature.
The code you review already passed a strict static-analysis allowlist (only math/statistics/json/datetime imports,
no eval/exec/open/os/subprocess/socket, no dunder attribute access), and will run in an isolated subprocess with
no credentials, no network, and a hard timeout. The function is decide_forex(as_of, positions, quotes, account),
returning a list of order intents. Your job is a SECOND, independent look for anything suspicious a mechanical
check might miss: obfuscated intent, unusual control flow that looks designed to evade review, or logic that
doesn't look like a real forex trading strategy at all. Respond with a short plain-text assessment (2-4
sentences) and end with exactly one line: "RISK: low" or "RISK: medium" or "RISK: high"."""


class UploadError(ValueError):
    """A strategy upload that can't be accepted as submitted -- the API layer
    maps this straight to a 400 with the specific reason."""


def _llm_risk_review(code: str, user_id: Optional[int] = None) -> str:
    if not HAS_ANTHROPIC:
        return "No LLM available in this environment -- risk review was not performed. RISK: unknown"
    client = make_llm_client(user_id=user_id)
    if client is None:
        return "No LLM API key configured -- risk review was not performed. RISK: unknown"
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=_RISK_REVIEW_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Review this uploaded forex strategy code:\n\n```python\n{code}\n```"}],
        )
        return extract_response_text(response).strip()
    except Exception as exc:
        return f"Risk review call failed: {exc}. RISK: unknown"


def submit_upload(
    *, name: str, description: str, code: str, interval_minutes: int, user_id: Optional[int] = None,
) -> Dict[str, Any]:
    if not name or not name.strip():
        raise UploadError("name is required")
    if not (MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES):
        raise UploadError(f"interval_minutes must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}")

    try:
        validate_code(code)
    except ForexStrategyCodeError as exc:
        raise UploadError(f"code rejected by static analysis: {exc}") from exc

    review_notes = _llm_risk_review(code, user_id=user_id)
    entry = repo.create_uploaded_strategy(
        name=name.strip(), description=description or "", code=code,
        interval_minutes=interval_minutes, review_status="pending", review_notes=review_notes,
    )
    return entry


def approve_upload(key: str) -> Dict[str, Any]:
    strategy = repo.get_strategy_def(key)
    if strategy is None or strategy["kind"] != "uploaded":
        raise UploadError(f"no uploaded forex strategy '{key}'")
    _set_review_status(key, "approved")
    return repo.get_strategy_def(key)


def reject_upload(key: str) -> Dict[str, Any]:
    strategy = repo.get_strategy_def(key)
    if strategy is None or strategy["kind"] != "uploaded":
        raise UploadError(f"no uploaded forex strategy '{key}'")
    _set_review_status(key, "rejected")
    return repo.get_strategy_def(key)


def _set_review_status(key: str, status: str) -> None:
    import sqlite3

    from dashboard.backend.domain.manual10 import repository as manual10_repo

    conn = sqlite3.connect(str(manual10_repo.DB_PATH))
    try:
        conn.execute("UPDATE manual10_strategies SET review_status = ? WHERE key = ?", (status, key))
        conn.commit()
    finally:
        conn.close()
