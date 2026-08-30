"""API Connections page: Alpaca live/paper, LLM providers, paper-wallet mode.

Robinhood is out of scope here -- it has its own OAuth flow and router
(``api/routers/robinhood_live.py``, ``POST /api/auth/robinhood/start``).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dashboard.backend.api.auth import get_current_user
from dashboard.backend.domain.connections.repository import (
    PROVIDER_FIELDS,
    MissingFieldError,
    UnknownProviderError,
    connection_store,
)

router = APIRouter(prefix="/v1/connections", tags=["connections"])


def _encryption_key_error(exc: RuntimeError) -> HTTPException:
    # ConnectionStore reuses domain.brokers.repository's Fernet key
    # (BROKER_TOKEN_ENCRYPTION_KEY) and raises a bare RuntimeError, fail-closed,
    # when it's unset or malformed -- same as the existing Robinhood link path.
    # Surfaced as a clean 503 rather than an unhandled 500 so a local operator
    # who hasn't set the key yet gets an actionable message instead of a stack
    # trace with no explanation.
    return HTTPException(status_code=503, detail=str(exc))


# Registered before "/{provider}" for the same reason "/paper-wallet" is:
# Starlette matches routes in registration order, and a literal path must
# come first or a GET to "/providers" would be swallowed by "/{provider}"
# with provider="providers".
#
# No auth: this is field *shape* only (which keys each provider needs), the
# same information PROVIDER_FIELDS already exposes to anyone reading the
# source -- never a credential value. It's what makes the frontend's card
# renderer data-driven (Sub-phase 2 of the Options-dashboard plan): the
# provider list becomes one source of truth instead of three hand-maintained
# copies (backend dict, HTML markup, JS maps).
@router.get("/providers")
def list_providers():
    return {"providers": PROVIDER_FIELDS}


@router.get("")
def list_connections(current_user: dict = Depends(get_current_user)):
    user_id = int(current_user["id"])
    try:
        connections = connection_store.list_public(user_id)
    except RuntimeError as exc:
        raise _encryption_key_error(exc) from exc
    return {
        "connections": connections,
        "paper_wallet": connection_store.get_paper_wallet_settings(user_id),
    }


class PaperWalletBody(BaseModel):
    mode: Optional[str] = Field(default=None)
    virtual_balance: Optional[float] = Field(default=None)


# Registered before the generic "/{provider}" routes below: Starlette matches
# routes in registration order, and a literal path must come first or a
# PUT/GET to "/paper-wallet" would be swallowed by "/{provider}" with
# provider="paper-wallet".
@router.get("/paper-wallet")
def get_paper_wallet(current_user: dict = Depends(get_current_user)):
    return connection_store.get_paper_wallet_settings(int(current_user["id"]))


@router.put("/paper-wallet")
def put_paper_wallet(body: PaperWalletBody, current_user: dict = Depends(get_current_user)):
    if body.mode is None and body.virtual_balance is None:
        raise HTTPException(status_code=400, detail="Provide mode and/or virtual_balance")
    try:
        return connection_store.set_paper_wallet_settings(
            int(current_user["id"]),
            mode=body.mode,
            virtual_balance=body.virtual_balance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ConnectionBody(BaseModel):
    api_key: Optional[str] = Field(default=None, max_length=256)
    secret_key: Optional[str] = Field(default=None, max_length=256)
    token: Optional[str] = Field(default=None, max_length=4096)
    url: Optional[str] = Field(default=None, max_length=512)
    # Tradovate + OANDA -- these providers' credential shapes don't fit the
    # generic api_key/secret_key pair above (Tradovate needs a login plus
    # separately-issued app credentials; OANDA needs a token and an account
    # id), so PROVIDER_FIELDS names them differently. Every field here must
    # have a matching entry in PROVIDER_FIELDS (domain/connections/
    # repository.py) or Pydantic silently drops it before connection_store
    # ever sees it -- exactly the bug this comment is here to prevent a
    # repeat of (caught live: the save button sent a correct body, and this
    # model discarded the fields whose names it didn't already know).
    username: Optional[str] = Field(default=None, max_length=256)
    password: Optional[str] = Field(default=None, max_length=256)
    cid: Optional[str] = Field(default=None, max_length=256)
    sec: Optional[str] = Field(default=None, max_length=256)
    account_spec: Optional[str] = Field(default=None, max_length=256)
    account_id: Optional[str] = Field(default=None, max_length=256)
    access_token: Optional[str] = Field(default=None, max_length=4096)


@router.put("/{provider}")
def save_connection(
    provider: str,
    body: ConnectionBody,
    current_user: dict = Depends(get_current_user),
):
    if provider not in PROVIDER_FIELDS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    try:
        return connection_store.save(int(current_user["id"]), provider, body.model_dump())
    except (MissingFieldError, UnknownProviderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _encryption_key_error(exc) from exc


@router.delete("/{provider}")
def delete_connection(provider: str, current_user: dict = Depends(get_current_user)):
    try:
        connection_store.delete(int(current_user["id"]), provider)
    except UnknownProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "connected": False}
