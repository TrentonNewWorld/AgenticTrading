# User Account Persistence Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the account system (`/api/auth/signup`, `/login`, `/me`, `/logout`) from silently losing every user on the next prod deploy, by giving it a storage backend that isn't wiped along with the ephemeral filesystem.

**Architecture:** `dashboard/backend/users.py`'s `UserStore` currently persists accounts in the same SQLite file as backtest data (`DB_PATH`). That file lives on the deployed Render service's local disk, which is **ephemeral on the free tier the service actually runs on** (confirmed via the Render API: 7 redeploys in the last 24h, `disk: null` on the service, `DATABASE_PATH` unset in prod so it falls back to the git-committed seed DB). Every redeploy replaces that file, deleting every account created since the previous deploy — this is the root cause, verified live: signup and login both work correctly right now (tested via curl against prod), so the bug is entirely about persistence, not auth logic.

The fix adds a **second, optional storage backend** for accounts only — a free-tier Postgres instance (Neon, chosen over Supabase/Render-Postgres because it never expires and auto-wakes without a manual dashboard restore) — selected via a new `USERS_DATABASE_URL` env var. Backtest data is untouched and stays on local SQLite. When the env var is unset (local dev, tests, CI), everything behaves exactly as it does today — zero risk to the existing 898-test suite.

**Tech Stack:** `psycopg[binary]==3.3.4` (new dependency), Postgres (Neon free tier), FastAPI/pytest (existing).

## Global Constraints

- Python 3.13.5 (matches `render.yaml`'s pin and the installed local interpreter).
- The existing `UserStore` class (SQLite) and its public method signatures must not change — `test_auth.py` and `test_v2_auth.py` construct it directly (`UserStore(db_path=...)`) and monkeypatch the module-level `user_store` singleton; both must keep working unmodified.
- New env var is `USERS_DATABASE_URL`. Do **not** reuse the existing `DATABASE_URL` prod env var — it's a dead/placeholder value from an earlier, unrelated attempt and its provenance is unknown; introducing a new, clearly-scoped name avoids inheriting that ambiguity.
- No client-side connection pooling (e.g. `psycopg_pool`) — use the provider's pooled connection string (Neon's `-pooler` host) instead. This is a low-traffic account system; a connection pool is unneeded complexity for now.
- `pytest dashboard/backend/tests/ -v` must stay fully green throughout (per `CLAUDE.md` / `[[dev-environment-and-tests]]`, a red test is a real regression).

---

### Task 1: Add the Postgres driver dependency

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Produces: the `psycopg` package (with `psycopg.connect`, `psycopg.rows.dict_row`, `psycopg.errors.UniqueViolation`) available to import in Task 2.

- [ ] **Step 1: Add the pin**

Add this line to `requirements.txt`, keeping the file's existing alphabetical-ish ordering (insert near other infra deps, e.g. after `psutil` if present, otherwise anywhere — the file isn't strictly sorted, just don't break an existing pin):

```
psycopg[binary]==3.3.4
```

- [ ] **Step 2: Install and verify**

Run: `pip install -r requirements.txt`

Then verify the import works:

```bash
python3 -c "import psycopg; from psycopg.rows import dict_row; from psycopg.errors import UniqueViolation; print(psycopg.__version__)"
```

Expected: prints `3.3.4` with no errors.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add psycopg for optional Postgres-backed user accounts"
```

---

### Task 2: Build `PostgresUserStore`

**Files:**
- Create: `dashboard/backend/users_postgres.py`
- Create: `dashboard/backend/tests/test_users_postgres.py`

**Interfaces:**
- Consumes (from `dashboard/backend/users.py`): `hash_password(password: str) -> str`, `verify_password(password: str, password_hash: str) -> bool`, `public_user(row) -> Dict[str, Any]`, `_utcnow() -> datetime`, `_utcnow_iso() -> str`.
- Produces: `PostgresUserStore` class with the exact same public method surface as `UserStore` — `__init__(self, database_url: str)`, `create_user(email, display_name, password) -> Dict`, `get_user_by_email(email) -> Optional[Dict]`, `get_user_by_id(user_id) -> Optional[Dict]`, `authenticate(email, password) -> Optional[Dict]`, `create_session(user_id) -> str`, `get_user_for_token(token) -> Optional[Dict]`, `delete_session(token) -> None`. Task 3 constructs this class and assigns it to the module-level `user_store` singleton.

This task needs a real Postgres to run its behavioral tests against. Start a throwaway one now (skip this if you already have `TEST_POSTGRES_URL` pointed at something disposable):

```bash
docker run --rm -d --name atl-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=atl_test -p 5433:5432 postgres:18-alpine
export TEST_POSTGRES_URL=postgresql://postgres:test@localhost:5433/atl_test
```

- [ ] **Step 1: Write the failing tests**

Create `dashboard/backend/tests/test_users_postgres.py`:

```python
"""
PostgresUserStore tests.

Two tiers:
1. Dispatch-logic tests (no live Postgres needed) - verify users.py picks
   the right store class based on USERS_DATABASE_URL.
2. Behavioral tests against a real Postgres - skipped unless
   TEST_POSTGRES_URL is set. Point it at a throwaway database, e.g.:
     docker run --rm -e POSTGRES_PASSWORD=test -e POSTGRES_DB=atl_test \
       -p 5433:5432 postgres:18-alpine
     export TEST_POSTGRES_URL=postgresql://postgres:test@localhost:5433/atl_test
"""

import os

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app

TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")

pg_only = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL not set; skipping live-Postgres tests",
)


def test_build_user_store_defaults_to_sqlite(monkeypatch):
    import dashboard.backend.users as users_module

    monkeypatch.delenv("USERS_DATABASE_URL", raising=False)
    store = users_module._build_user_store()
    assert isinstance(store, users_module.UserStore)


def test_build_user_store_picks_postgres_when_url_set(monkeypatch):
    import dashboard.backend.users as users_module
    import dashboard.backend.users_postgres as users_postgres_module

    created = {}

    class FakePostgresUserStore:
        def __init__(self, database_url):
            created["database_url"] = database_url

    monkeypatch.setattr(users_postgres_module, "PostgresUserStore", FakePostgresUserStore)
    monkeypatch.setenv("USERS_DATABASE_URL", "postgresql://fake/db")

    store = users_module._build_user_store()

    assert isinstance(store, FakePostgresUserStore)
    assert created["database_url"] == "postgresql://fake/db"


@pytest.fixture
def temp_postgres_store():
    from dashboard.backend.users_postgres import PostgresUserStore

    store = PostgresUserStore(TEST_POSTGRES_URL)
    with store._get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM auth_sessions")
            cur.execute("DELETE FROM users")
    yield store


@pytest.fixture
def pg_client(temp_postgres_store, monkeypatch):
    import dashboard.backend.users as users_module

    monkeypatch.setattr(users_module, "user_store", temp_postgres_store)
    return TestClient(app)


@pg_only
def test_signup_login_me_logout_flow_postgres(pg_client):
    signup = pg_client.post(
        "/api/auth/signup",
        json={"email": "alice@example.com", "display_name": "Alice", "password": "securepass1"},
    )
    assert signup.status_code == 200
    signup_data = signup.json()
    assert signup_data["user"]["email"] == "alice@example.com"
    assert signup_data["user"]["display_name"] == "Alice"
    assert signup_data["user"]["role"] == "user"
    assert "password_hash" not in signup_data["user"]
    assert signup_data["token"]

    duplicate = pg_client.post(
        "/api/auth/signup",
        json={"email": "alice@example.com", "display_name": "Alice 2", "password": "securepass1"},
    )
    assert duplicate.status_code == 409

    login = pg_client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "securepass1"},
    )
    assert login.status_code == 200
    token = login.json()["token"]

    me = pg_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "alice@example.com"

    logout = pg_client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 200

    me_after = pg_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_after.status_code == 401


@pg_only
def test_login_invalid_password_postgres(pg_client):
    pg_client.post(
        "/api/auth/signup",
        json={"email": "bob@example.com", "display_name": "Bob", "password": "securepass1"},
    )
    response = pg_client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest dashboard/backend/tests/test_users_postgres.py -v`

Expected (all imports of `users_postgres` are inside function bodies, not at module top, so this is a mix of runtime failures, not a collection error):
- `test_build_user_store_defaults_to_sqlite` — FAILS: `AttributeError: module 'dashboard.backend.users' has no attribute '_build_user_store'`
- `test_build_user_store_picks_postgres_when_url_set` — FAILS: `ModuleNotFoundError: No module named 'dashboard.backend.users_postgres'`
- `test_signup_login_me_logout_flow_postgres` / `test_login_invalid_password_postgres` — if `TEST_POSTGRES_URL` is set (per the preamble above), ERROR on fixture setup with the same `ModuleNotFoundError` (raised inside `temp_postgres_store`); if unset, SKIPPED instead.

- [ ] **Step 3: Implement `PostgresUserStore`**

Create `dashboard/backend/users_postgres.py`:

```python
"""
Postgres-backed UserStore implementation.

Selected instead of the default SQLite UserStore when USERS_DATABASE_URL is
set (see users.py's _build_user_store). Exists because the SQLite UserStore
shares DB_PATH with backtest data, and the deployed backend runs on a
disk-less Render free-tier host where that file resets on every deploy --
silently deleting every account (see CLAUDE.md gotchas).
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import psycopg
from psycopg.rows import dict_row

from dashboard.backend.users import _utcnow, _utcnow_iso, hash_password, public_user, verify_password

SESSION_TTL_DAYS = 7


class PostgresUserStore:
    """Minimal user + auth session persistence, backed by Postgres."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._init_schema()

    def _get_connection(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _init_schema(self) -> None:
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        display_name TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'user',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        token TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id
                    ON auth_sessions(user_id)
                    """
                )

    def create_user(self, email: str, display_name: str, password: str) -> Dict[str, Any]:
        normalized_email = email.strip().lower()
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO users (email, display_name, password_hash, role, created_at)
                        VALUES (%s, %s, %s, 'user', %s)
                        RETURNING *
                        """,
                        (normalized_email, display_name.strip(), hash_password(password), _utcnow_iso()),
                    )
                    row = cur.fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError("email_already_registered") from exc
        return public_user(row)

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM users WHERE email = %s",
                    (email.strip().lower(),),
                )
                row = cur.fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
        return dict(row) if row else None

    def authenticate(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        user = self.get_user_by_email(email)
        if not user:
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        return user

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = _utcnow()
        created_at = now.replace(microsecond=0).isoformat()
        expires_at = (now + timedelta(days=SESSION_TTL_DAYS)).replace(microsecond=0).isoformat()
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auth_sessions (token, user_id, created_at, expires_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (token, user_id, created_at, expires_at),
                )
        return token

    def get_user_for_token(self, token: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT users.*
                    FROM auth_sessions
                    JOIN users ON users.id = auth_sessions.user_id
                    WHERE auth_sessions.token = %s
                    """,
                    (token,),
                )
                row = cur.fetchone()
                if not row:
                    return None

                cur.execute(
                    "SELECT expires_at FROM auth_sessions WHERE token = %s",
                    (token,),
                )
                session_row = cur.fetchone()

        if not session_row:
            return None

        expires_at = datetime.fromisoformat(session_row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < _utcnow():
            self.delete_session(token)
            return None

        return dict(row)

    def delete_session(self, token: str) -> None:
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth_sessions WHERE token = %s", (token,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest dashboard/backend/tests/test_users_postgres.py -v`

Expected: both `test_build_user_store_defaults_to_sqlite` and `test_build_user_store_picks_postgres_when_url_set` still **FAIL**, but now both with the same error — `AttributeError: module 'dashboard.backend.users' has no attribute '_build_user_store'` (the `users_postgres` module now exists and imports fine, so that half of the second test's setup succeeds; what's still missing is `_build_user_store` itself, added in Task 3). The two `@pg_only` behavioral tests **PASS** if `TEST_POSTGRES_URL` is set, or **SKIP** if not.

This is expected — Task 3 makes the two dispatch tests pass. Confirm the `@pg_only` tests specifically pass now if you have a test Postgres running:

```bash
pytest dashboard/backend/tests/test_users_postgres.py -v -k postgres
```

Expected: `test_signup_login_me_logout_flow_postgres` and `test_login_invalid_password_postgres` PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/users_postgres.py dashboard/backend/tests/test_users_postgres.py
git commit -m "feat(auth): add Postgres-backed UserStore implementation"
```

---

### Task 3: Wire up the storage backend selector

**Files:**
- Modify: `dashboard/backend/users.py`

**Interfaces:**
- Consumes: `PostgresUserStore` from `dashboard/backend/users_postgres.py` (Task 2), imported lazily inside the function to avoid a circular import (`users_postgres.py` imports from `users.py`).
- Produces: `_build_user_store()` function; `user_store` module-level singleton unchanged in name/shape, so `from dashboard.backend.users import public_user, user_store` (used by `api/auth.py`) needs no changes.

- [ ] **Step 1: Confirm the dispatch tests still fail as expected**

Run: `pytest dashboard/backend/tests/test_users_postgres.py -v -k build_user_store`

Expected: both FAIL with `AttributeError: ... has no attribute '_build_user_store'` (confirmed already in Task 2 Step 4 — just re-checking before implementing).

- [ ] **Step 2: Add `os` import and the factory function**

In `dashboard/backend/users.py`, add `import os` to the top-of-file imports (alongside the existing `hashlib`, `secrets`, `sqlite3` imports at lines 5-7):

```python
import hashlib
import os
import secrets
import sqlite3
```

Then replace the final line of the file —

```python
user_store = UserStore()
```

— with:

```python
def _build_user_store():
    database_url = os.getenv("USERS_DATABASE_URL")
    if database_url:
        from dashboard.backend.users_postgres import PostgresUserStore

        return PostgresUserStore(database_url)
    return UserStore()


user_store = _build_user_store()
```

Note on failure behavior: `PostgresUserStore.__init__` connects immediately (via `_init_schema()`), same as `UserStore.__init__` does against SQLite today. So if `USERS_DATABASE_URL` is set but unreachable/malformed, the app fails to start rather than silently falling back to SQLite — this is intentional. A silent fallback would quietly reintroduce the exact bug this plan fixes (accounts persisting somewhere that gets wiped on the next deploy) with no signal that it happened.

- [ ] **Step 3: Run the dispatch tests to verify they pass**

Run: `pytest dashboard/backend/tests/test_users_postgres.py -v -k build_user_store`

Expected: both PASS.

- [ ] **Step 4: Run the full backend suite to confirm no regressions**

Run: `pytest dashboard/backend/tests/ -v`

Expected: same pass count as before this plan (no `USERS_DATABASE_URL` is set in the test environment, so every existing test still gets the plain SQLite `UserStore` exactly as before). If you see the stale-bytecode `test_deleted_shim_is_not_importable` failure, that's the known phantom (`rm -rf dashboard/backend/engines dashboard/backend/services`), not a regression from this change.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/users.py
git commit -m "feat(auth): select Postgres or SQLite UserStore via USERS_DATABASE_URL"
```

---

### Task 4: Document the new env var and the incident

**Files:**
- Modify: `.env.example`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add `USERS_DATABASE_URL` to `.env.example`**

In `.env.example`, after the existing `# Database` block (`DATABASE_PATH=...`), add:

```
# User accounts (optional). When set, signup/login/session data is stored in
# this Postgres database instead of the local SQLite file above -- required
# on hosts with an ephemeral/disk-less filesystem (e.g. Render free tier),
# where DATABASE_PATH resets on every deploy and would otherwise silently
# delete every account. Use a pooled connection string if your provider
# offers one (e.g. Neon's "-pooler" host) since each store call opens a new
# connection. Leave unset for local dev -- falls back to DATABASE_PATH.
# USERS_DATABASE_URL=postgresql://user:password@host/dbname
```

- [ ] **Step 2: Document it in `CLAUDE.md`**

In the "Environment & credentials" section, add a bullet after the existing `DATABASE_PATH` bullet:

```markdown
- `USERS_DATABASE_URL` (optional): when set, `dashboard/backend/users.py` stores accounts/sessions in this Postgres database instead of the local SQLite `DB_PATH`. See the Gotchas entry below for why this exists.
```

In the "Gotchas" section, add a new bullet:

```markdown
- **User accounts were silently lost on every prod redeploy until 2026-07 (see `docs/superpowers/plans/2026-07-08-user-account-persistence-fix.md`).** `users.py` originally shared `DB_PATH` with backtest data; on the live Render service (free tier, `disk: null`, `DATABASE_PATH` unset) that file resets to the git-committed seed DB on every deploy, deleting the `users`/`auth_sessions` tables with no error surfaced anywhere. The fix is an optional Postgres backend selected via `USERS_DATABASE_URL` — set it in prod (see `[[render-prod-architecture]]`); leave it unset for local dev/tests, which keep using SQLite exactly as before.
```

- [ ] **Step 3: Commit**

```bash
git add .env.example CLAUDE.md
git commit -m "docs: document USERS_DATABASE_URL and the account-persistence incident"
```

---

### Task 5: Provision Neon and deploy — MANUAL, human only

Not eligible for autonomous/subagent execution: it needs a Neon account and Render dashboard secret entry, plus a merge from Allan (per `[[render-prod-architecture]]`, `FlyM1ss` has no push access to `TrentonNewWorld/NewWorldTrading`, and Render deploys from that fork's `main`, not `TrentonNewWorld/NewWorldTrading` `main`).

**Files:** none — this is an operational task.

- [ ] **Step 1: Create the Neon project**

Sign up / log in at neon.tech (free, no credit card). Create a new project (any region close to Render's `oregon` region is a reasonable default, e.g. `us-west-2`). Neon creates a default database automatically.

- [ ] **Step 2: Copy the pooled connection string**

In the Neon project dashboard, copy the **pooled** connection string (the one with `-pooler` in the hostname) — this is what `USERS_DATABASE_URL` should be set to, per the Global Constraints note about avoiding client-side pooling.

- [ ] **Step 3: Open a PR with Tasks 1-4 and get it merged to `TrentonNewWorld/NewWorldTrading` main**

Standard PR flow. Nothing in this PR touches prod behavior yet (`USERS_DATABASE_URL` is unset everywhere until Step 4), so it's safe to merge on its own.

- [ ] **Step 4: Cross-fork PR to Allan, get it merged, confirm deploy**

Open `the upstream org:main → TrentonNewWorld:main` (per existing process in `[[render-prod-architecture]]`). Once Allan merges, Render auto-deploys — but this deploy alone doesn't turn on Postgres yet, since the env var isn't set.

- [ ] **Step 5: Set `USERS_DATABASE_URL` in the Render dashboard**

In the Render dashboard for service `srv-d7lbmpjbc2fs73bcr6t0` ("NewWorldTrading") → Environment, add `USERS_DATABASE_URL` with the pooled connection string from Step 2. Saving triggers a redeploy automatically.

- [ ] **Step 6: Verify — signup, then force a second deploy, then confirm the account survived**

After the redeploy from Step 5 finishes:

```bash
curl -s -X POST https://newworldtrading.onrender.com/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"persistence-verify@example.com","display_name":"Verify","password":"testpassword123"}'
```

Expected: `200` with a `user`/`token` payload.

Trigger any redeploy (e.g. a trivial docs commit merged through the same PR flow, or manually redeploy from the Render dashboard), wait for it to finish, then:

```bash
curl -s -X POST https://newworldtrading.onrender.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"persistence-verify@example.com","password":"testpassword123"}'
```

Expected: `200` with the **same** account, proving it survived the deploy. If this returns `401`, the env var isn't taking effect (check for typos, and check Render's deploy logs for a psycopg connection error at startup) — do not consider this task done until this check passes.

Any accounts created on prod before this task (including the `debug-investigation-probe@example.com` / `id=5` account created during the original investigation) will **not** carry over — they lived in the SQLite file, which this migration doesn't touch or attempt to preserve. That's expected and was already unrecoverable (the very next deploy would have deleted them regardless).
