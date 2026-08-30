"""Discord OAuth helpers for linking website accounts to Discord user ids."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

DISCORD_AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_ME_URL = "https://discord.com/api/users/@me"
STATE_TTL_SECONDS = 600


def discord_client_id() -> str:
    return (os.getenv("DISCORD_CLIENT_ID") or "").strip()


def discord_client_secret() -> str:
    return (os.getenv("DISCORD_CLIENT_SECRET") or "").strip()


def discord_redirect_uri() -> str:
    return (os.getenv("DISCORD_REDIRECT_URI") or "").strip()


def discord_guild_channel_url() -> str:
    return (
        os.getenv("DISCORD_GUILD_CHANNEL_URL")
        or os.getenv("DISCORD_SERVER_URL")
        or "https://discord.gg/xyC3MQAWNj"
    ).strip()


def discord_bot_api_secret() -> str:
    return (os.getenv("DISCORD_BOT_API_SECRET") or "").strip()


def oauth_configured() -> bool:
    return bool(discord_client_id() and discord_client_secret() and discord_redirect_uri())


def _state_signing_key() -> bytes:
    # Prefer an explicit override, else the OAuth client secret (which
    # ``oauth_configured()`` already requires for the flow to run at all, so the
    # bare dev fallback below is only ever reached in an unconfigured dev box).
    raw = (
        os.getenv("DISCORD_OAUTH_STATE_SECRET")
        or discord_client_secret()
        or "dev-discord-oauth-state"
    )
    return raw.encode("utf-8")


def mint_oauth_state(user_id: int) -> str:
    payload = {
        "uid": int(user_id),
        "exp": int(time.time()) + STATE_TTL_SECONDS,
        "n": secrets.token_hex(8),
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    raw = raw.rstrip("=")
    sig = hmac.new(_state_signing_key(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def parse_oauth_state(state: str) -> int:
    try:
        raw, sig = state.rsplit(".", 1)
    except ValueError as exc:
        raise ValueError("invalid_state") from exc
    expected = hmac.new(_state_signing_key(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("invalid_state")
    pad = "=" * (-len(raw) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw + pad).decode())
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_state") from exc
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("expired_state")
    user_id = payload.get("uid")
    if user_id is None:
        raise ValueError("invalid_state")
    return int(user_id)


def build_authorize_url(state: str) -> str:
    if not oauth_configured():
        raise RuntimeError("discord_oauth_not_configured")
    query = urlencode(
        {
            "client_id": discord_client_id(),
            "response_type": "code",
            "redirect_uri": discord_redirect_uri(),
            "scope": "identify",
            "state": state,
            "prompt": "consent",
        }
    )
    return f"{DISCORD_AUTHORIZE_URL}?{query}"


def exchange_code_for_access_token(code: str) -> str:
    if not oauth_configured():
        raise RuntimeError("discord_oauth_not_configured")
    data = {
        "client_id": discord_client_id(),
        "client_secret": discord_client_secret(),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": discord_redirect_uri(),
    }
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            DISCORD_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"discord_token_exchange_failed:{resp.status_code}")
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise RuntimeError("discord_token_missing")
    return str(token)


def fetch_discord_user(access_token: str) -> dict[str, Any]:
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(
            DISCORD_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"discord_me_failed:{resp.status_code}")
    body = resp.json()
    if not body.get("id"):
        raise RuntimeError("discord_user_id_missing")
    return body


def verify_bot_secret(provided: Optional[str]) -> bool:
    expected = discord_bot_api_secret()
    if not expected or not provided:
        return False
    # compare_digest on str rejects non-ASCII; always compare utf-8 bytes.
    return hmac.compare_digest(
        expected.encode("utf-8"),
        provided.strip().encode("utf-8"),
    )
