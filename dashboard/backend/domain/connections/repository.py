"""SQLite store for per-user API connections + paper-wallet funding mode.

Reuses the same Fernet key (``BROKER_TOKEN_ENCRYPTION_KEY``) and fail-closed
behavior as ``domain.brokers.repository`` -- one encryption key for every
credential this app stores, rather than a second secret operators have to
remember to set.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dashboard.backend.database import DB_PATH
from dashboard.backend.domain.brokers.repository import _decrypt, _encrypt

# provider -> (required fields, optional fields)
PROVIDER_FIELDS: Dict[str, Dict[str, Any]] = {
    "alpaca_live": {"required": ["api_key", "secret_key"], "optional": []},
    "alpaca_paper": {"required": ["api_key", "secret_key"], "optional": []},
    # A distinct Alpaca account from alpaca_live -- an IRA has its own
    # account number and its own key pair (Alpaca rejects one account's
    # keys against another's endpoint). See
    # infrastructure/brokers/alpaca_ira.py's docstring: balance-visibility
    # only for now (domain/wallets.py reads it so the IRA's real balance
    # counts toward the Stocks dashboard and the Home page's combined
    # portfolio total) -- no order-execution path targets this account yet.
    "alpaca_ira": {"required": ["api_key", "secret_key"], "optional": []},
    "anthropic_subscription": {"required": ["token"], "optional": []},
    "anthropic_api": {"required": ["api_key"], "optional": []},
    "openrouter": {"required": ["api_key"], "optional": []},
    # Fronts all 7 Competition Leaderboard AI models (Claude, GPT, Gemini,
    # Qwen, DeepSeek, Nemotron, +1) through one Anthropic-compatible gateway
    # -- see infrastructure/llm/providers/commonstack.py, which already reads
    # COMMONSTACK_API_KEY as an env var; this is the missing Connections-page
    # entry for that same key so a user can supply their own instead.
    "commonstack": {"required": ["api_key"], "optional": []},
    "local": {"required": ["url"], "optional": ["api_key"]},
    # Kalshi's demo API key + RSA private key -- see
    # infrastructure/brokers/kalshi_paper.py's docstring: unlocks real
    # demo-exchange fills instead of local simulation. The client also
    # supports a production key, but no call site targets it yet, so a
    # production key stored here is currently inert (real-money Kalshi
    # trading is not wired up -- see that module's docstring).
    "kalshi": {"required": ["api_key", "secret_key"], "optional": []},
    # A funded Polygon wallet's private key -- real financial custody
    # material, same handling as every other credential here (encrypted at
    # rest via the shared Fernet key). See
    # infrastructure/brokers/polymarket_paper.py's docstring: Polymarket has
    # no demo mode, so this only ever enables real-money trading, off by
    # default (Prediction's paper mode never needs it).
    "polymarket": {"required": ["api_key"], "optional": []},
    # Tradovate demo/simulation account -- see
    # infrastructure/brokers/tradovate_paper.py's TradovateCredentials for
    # why all 6 fields are required (username/password log into the platform;
    # cid/sec are the app-level API credentials Tradovate issues separately;
    # account_spec/account_id select which sub-account under that login to
    # trade). Resolved without per-user scoping -- see
    # get_credentials_any_user()'s docstring for why: Futures strategies have
    # no owner to begin with (select/activate aren't user-scoped either), so
    # this is one shared connection, same as Manual10's Alpaca-live wallet.
    "tradovate": {
        "required": ["username", "password", "cid", "sec", "account_spec", "account_id"], "optional": [],
    },
    # OANDA v20 practice/demo account -- infrastructure/brokers/
    # oanda_practice.py's OandaCredentials. Same shared-connection model as
    # tradovate above (Forex strategies aren't user-owned either).
    "oanda": {"required": ["access_token", "account_id"], "optional": []},
}

# The field shown (masked) in the public/status payload so a user can tell
# *which* credential is connected without ever re-serving the plaintext.
_HINT_FIELD = {
    "alpaca_live": "api_key",
    "alpaca_paper": "api_key",
    "alpaca_ira": "api_key",
    "anthropic_subscription": "token",
    "anthropic_api": "api_key",
    "openrouter": "api_key",
    "commonstack": "api_key",
    "local": "url",
    "kalshi": "api_key",
    "polymarket": "api_key",
    "tradovate": "username",
    "oanda": "account_id",
}

_PAPER_WALLET_MODES = ("alpaca", "virtual")


class UnknownProviderError(ValueError):
    pass


class MissingFieldError(ValueError):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"


class ConnectionStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path or DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        return conn

    def _init_schema(self) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS api_connections (
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                credentials_enc TEXT NOT NULL,
                connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, provider)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_wallet_settings (
                user_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'virtual',
                virtual_balance REAL NOT NULL DEFAULT 10000,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
        conn.close()

    # -- connections ---------------------------------------------------

    def list_public(self, user_id: int) -> Dict[str, Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM api_connections WHERE user_id = ?",
            (int(user_id),),
        )
        rows = {row["provider"]: dict(row) for row in cursor.fetchall()}
        conn.close()

        result: Dict[str, Dict[str, Any]] = {}
        for provider in PROVIDER_FIELDS:
            row = rows.get(provider)
            if not row:
                result[provider] = {"connected": False, "hint": None, "updated_at": None}
                continue
            try:
                fields = json.loads(_decrypt(row["credentials_enc"]))
            except Exception:
                fields = {}
            hint_field = _HINT_FIELD.get(provider)
            raw_hint = fields.get(hint_field) if hint_field else None
            hint = _mask(raw_hint) if hint_field != "url" else raw_hint
            result[provider] = {
                "connected": True,
                "hint": hint,
                "updated_at": row.get("updated_at"),
            }
        return result

    def save(self, user_id: int, provider: str, fields: Dict[str, str]) -> Dict[str, Any]:
        spec = PROVIDER_FIELDS.get(provider)
        if not spec:
            raise UnknownProviderError(f"unknown provider: {provider}")
        clean: Dict[str, str] = {}
        for key in spec["required"]:
            value = (fields.get(key) or "").strip()
            if not value:
                raise MissingFieldError(f"{key} is required for {provider}")
            clean[key] = value
        for key in spec["optional"]:
            value = (fields.get(key) or "").strip()
            if value:
                clean[key] = value

        now = _utcnow_iso()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO api_connections (user_id, provider, credentials_enc, connected_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, provider) DO UPDATE SET
                credentials_enc = excluded.credentials_enc,
                updated_at = excluded.updated_at
            """,
            (int(user_id), provider, _encrypt(json.dumps(clean)), now, now),
        )
        conn.commit()
        conn.close()
        return self.list_public(user_id)[provider]

    def delete(self, user_id: int, provider: str) -> bool:
        if provider not in PROVIDER_FIELDS:
            raise UnknownProviderError(f"unknown provider: {provider}")
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM api_connections WHERE user_id = ? AND provider = ?",
            (int(user_id), provider),
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    def get_credentials(self, user_id: int, provider: str) -> Optional[Dict[str, str]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT credentials_enc FROM api_connections WHERE user_id = ? AND provider = ?",
            (int(user_id), provider),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        try:
            return json.loads(_decrypt(row["credentials_enc"]))
        except Exception:
            return None

    def get_credentials_any_user(self, provider: str) -> Optional[Dict[str, str]]:
        """Like ``get_credentials``, but not scoped to a specific user --
        for shared-account features that have no per-record owner to resolve
        against in the first place (Futures/Forex strategies aren't
        user-scoped anywhere: select/activate/tick all operate on one shared
        account, the same posture Manual10 already has for its Alpaca-live
        wallet). Returns whichever connection for this provider was saved
        most recently. In a genuinely multi-tenant hosted deployment this
        means "whichever user connected it last wins" for these two shared
        features specifically -- an explicit, documented trade-off matching
        the rest of Futures/Forex's existing single-shared-account design,
        not a silent behavior difference from every other per-user
        credential in this store."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT credentials_enc FROM api_connections WHERE provider = ? ORDER BY updated_at DESC LIMIT 1",
            (provider,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        try:
            return json.loads(_decrypt(row["credentials_enc"]))
        except Exception:
            return None

    # -- paper wallet mode ----------------------------------------------

    def get_paper_wallet_settings(self, user_id: int) -> Dict[str, Any]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM paper_wallet_settings WHERE user_id = ?",
            (int(user_id),),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return {"mode": "virtual", "virtual_balance": 10000.0}
        return {"mode": row["mode"], "virtual_balance": float(row["virtual_balance"])}

    def set_paper_wallet_settings(
        self,
        user_id: int,
        *,
        mode: Optional[str] = None,
        virtual_balance: Optional[float] = None,
    ) -> Dict[str, Any]:
        if mode is not None and mode not in _PAPER_WALLET_MODES:
            raise ValueError(f"mode must be one of {_PAPER_WALLET_MODES}")
        if virtual_balance is not None and virtual_balance <= 0:
            raise ValueError("virtual_balance must be positive")

        current = self.get_paper_wallet_settings(user_id)
        next_mode = mode if mode is not None else current["mode"]
        next_balance = virtual_balance if virtual_balance is not None else current["virtual_balance"]
        now = _utcnow_iso()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO paper_wallet_settings (user_id, mode, virtual_balance, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                mode = excluded.mode,
                virtual_balance = excluded.virtual_balance,
                updated_at = excluded.updated_at
            """,
            (int(user_id), next_mode, float(next_balance), now),
        )
        conn.commit()
        conn.close()
        return {"mode": next_mode, "virtual_balance": float(next_balance)}


connection_store = ConnectionStore()
