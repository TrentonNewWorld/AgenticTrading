"""domain/execution_settings.py + api/routers/execution_settings.py -- the
DB-backed override behind the "Live Trading" account-menu switch.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import dashboard.backend.users as users_module
from dashboard.backend.domain import execution_settings
from dashboard.backend.tests.auth_cookies_helpers import _cookie_session_token


@pytest.fixture(autouse=True)
def _isolated_settings_db(monkeypatch):
    db_path = tempfile.mktemp(suffix=".db")
    monkeypatch.setattr(execution_settings, "DB_PATH", db_path)
    execution_settings._init_schema()
    yield


@pytest.fixture
def isolated_auth(monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard.backend.app import app

    with tempfile.TemporaryDirectory() as tmpdir:
        store = users_module.UserStore(db_path=Path(tmpdir) / "users.db")
        monkeypatch.setattr(users_module, "user_store", store)
        yield TestClient(app), store


def _promote(store, user_id):
    return store.apply_admin_patch(user_id, role="admin")


def _signup(client, store, email="admin@example.com", make_admin=True):
    resp = client.post("/api/auth/signup", json={"email": email, "display_name": "A", "password": "securepass1"})
    assert resp.status_code == 200, resp.text
    user_id = resp.json()["user"]["id"]
    if make_admin:
        _promote(store, user_id)
    token = _cookie_session_token(client)
    return {"Authorization": f"Bearer {token}"}, user_id


# ---------------------------------------------------------------------------
# domain/execution_settings.py
# ---------------------------------------------------------------------------

def test_get_override_is_none_when_never_set():
    assert execution_settings.get_override("alpaca_live_execute") is None


def test_set_and_get_override_round_trips():
    execution_settings.set_override("alpaca_live_execute", True, user_id=7)
    assert execution_settings.get_override("alpaca_live_execute") is True
    status = execution_settings.get_status("alpaca_live_execute")
    assert status["enabled"] is True
    assert status["updated_by_user_id"] == 7


def test_set_override_can_flip_back_off():
    execution_settings.set_override("alpaca_live_execute", True)
    execution_settings.set_override("alpaca_live_execute", False)
    assert execution_settings.get_override("alpaca_live_execute") is False


def test_execute_enabled_falls_back_to_env_var_when_never_toggled(monkeypatch):
    from dashboard.backend.execution.alpaca_live_service import execute_enabled

    monkeypatch.setenv("ALPACA_LIVE_EXECUTE", "true")
    assert execute_enabled() is True
    monkeypatch.setenv("ALPACA_LIVE_EXECUTE", "false")
    assert execute_enabled() is False


def test_execute_enabled_prefers_the_db_override_over_the_env_var(monkeypatch):
    """The whole point of this feature: the account-menu switch must win,
    not just supplement, the env var -- otherwise a hosted deploy's
    ALPACA_LIVE_EXECUTE=false would silently veto an admin's explicit
    on-switch, defeating the reason it exists."""
    from dashboard.backend.execution.alpaca_live_service import execute_enabled

    monkeypatch.setenv("ALPACA_LIVE_EXECUTE", "false")
    execution_settings.set_override("alpaca_live_execute", True)
    assert execute_enabled() is True

    execution_settings.set_override("alpaca_live_execute", False)
    monkeypatch.setenv("ALPACA_LIVE_EXECUTE", "true")
    assert execute_enabled() is False


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

def test_get_settings_requires_admin(isolated_auth):
    client, store = isolated_auth
    headers, _ = _signup(client, store, make_admin=False)
    resp = client.get("/api/admin/execution-settings", headers=headers)
    assert resp.status_code == 403


def test_get_settings_requires_signed_in(isolated_auth):
    client, _store = isolated_auth
    resp = client.get("/api/admin/execution-settings")
    assert resp.status_code == 401


def test_admin_can_read_and_toggle_the_switch(isolated_auth):
    client, store = isolated_auth
    headers, user_id = _signup(client, store, make_admin=True)

    initial = client.get("/api/admin/execution-settings", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["alpaca_live_execute"]["override"] is None

    turned_on = client.put(
        "/api/admin/execution-settings/alpaca-live-execute", headers=headers, json={"enabled": True},
    )
    assert turned_on.status_code == 200
    assert turned_on.json()["alpaca_live_execute"]["effective"] is True

    listed = client.get("/api/admin/execution-settings", headers=headers)
    assert listed.json()["alpaca_live_execute"]["override"] is True

    turned_off = client.put(
        "/api/admin/execution-settings/alpaca-live-execute", headers=headers, json={"enabled": False},
    )
    assert turned_off.json()["alpaca_live_execute"]["effective"] is False


def test_non_admin_cannot_toggle_the_switch(isolated_auth):
    client, store = isolated_auth
    headers, _ = _signup(client, store, make_admin=False)
    resp = client.put(
        "/api/admin/execution-settings/alpaca-live-execute", headers=headers, json={"enabled": True},
    )
    assert resp.status_code == 403
