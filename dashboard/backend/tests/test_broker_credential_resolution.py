"""Sub-phase 3 of the Options-dashboard plan: a signed-in user's
Connections-saved credentials must actually be used at request time --
before this, ``domain.connections.repository.ConnectionStore`` stored
whatever a user saved on the Connections page but nothing ever read it back,
so every broker/LLM call was env-var/file only regardless of what was saved.

These tests pin the fix at its lowest level (the shared resolvers), not
through the full HTTP stack -- the resolvers are what every broker/LLM call
site (Strategy Catalog's Run in Paper/Live, the shared LLM client factory)
now goes through.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import dashboard.backend.domain.brokers.repository as brokers_repo
import dashboard.backend.domain.connections.repository as connections_repo


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv(brokers_repo._KEY_ENV_VAR, Fernet.generate_key().decode())
    with tempfile.TemporaryDirectory() as tmpdir:
        cs = connections_repo.ConnectionStore(db_path=Path(tmpdir) / "connections.db")
        monkeypatch.setattr(connections_repo, "connection_store", cs)
        yield cs


# ---------------------------------------------------------------------------
# infrastructure/brokers/credentials.py -- Alpaca paper/live
# ---------------------------------------------------------------------------

def test_resolve_alpaca_credentials_none_without_user_id(store):
    from dashboard.backend.infrastructure.brokers.credentials import resolve_alpaca_credentials

    assert resolve_alpaca_credentials(None, "alpaca_paper") is None


def test_resolve_alpaca_credentials_none_when_nothing_saved(store):
    from dashboard.backend.infrastructure.brokers.credentials import resolve_alpaca_credentials

    assert resolve_alpaca_credentials(1, "alpaca_paper") is None


def test_resolve_alpaca_credentials_prefers_connections_store(store):
    from dashboard.backend.infrastructure.brokers.credentials import resolve_alpaca_credentials

    store.save(1, "alpaca_paper", {"api_key": "PKCONNSAVED", "secret_key": "connsecret"})
    assert resolve_alpaca_credentials(1, "alpaca_paper") == ("PKCONNSAVED", "connsecret")


def test_alpaca_paper_client_prefers_connections_key_over_env_var(store, monkeypatch):
    from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient

    monkeypatch.setenv("ALPACA_API_KEY", "ENVKEY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "envsecret")
    store.save(1, "alpaca_paper", {"api_key": "PKCONNSAVED", "secret_key": "connsecret"})

    client = AlpacaPaperTradingClient(user_id=1)
    assert client.api_key == "PKCONNSAVED"
    assert client.secret_key == "connsecret"


def test_alpaca_paper_client_falls_through_to_env_var_when_nothing_saved(store, monkeypatch):
    from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient

    monkeypatch.setenv("ALPACA_API_KEY", "ENVKEY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "envsecret")

    client = AlpacaPaperTradingClient(user_id=1)
    assert client.api_key == "ENVKEY"
    assert client.secret_key == "envsecret"


def test_alpaca_paper_client_falls_through_after_connections_key_removed(store, monkeypatch):
    from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient

    monkeypatch.setenv("ALPACA_API_KEY", "ENVKEY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "envsecret")
    store.save(1, "alpaca_paper", {"api_key": "PKCONNSAVED", "secret_key": "connsecret"})
    store.delete(1, "alpaca_paper")

    client = AlpacaPaperTradingClient(user_id=1)
    assert client.api_key == "ENVKEY"
    assert client.secret_key == "envsecret"


def test_alpaca_paper_client_without_user_id_is_env_var_only(store, monkeypatch):
    """No user_id (unattended context, e.g. the manual10 scheduler) must
    behave exactly as before this sub-phase, even if some other user has a
    Connections-saved key -- an unattended caller has no identity to check
    the store against and must never pick up an unrelated user's key."""
    from dashboard.backend.infrastructure.brokers.alpaca_paper import AlpacaPaperTradingClient

    monkeypatch.setenv("ALPACA_API_KEY", "ENVKEY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "envsecret")
    store.save(1, "alpaca_paper", {"api_key": "PKCONNSAVED", "secret_key": "connsecret"})

    client = AlpacaPaperTradingClient()
    assert client.api_key == "ENVKEY"
    assert client.secret_key == "envsecret"


def test_alpaca_live_client_prefers_connections_key_over_env_var(store, monkeypatch):
    from dashboard.backend.infrastructure.brokers.alpaca_live import AlpacaLiveTradingClient

    monkeypatch.setenv("ALPACA_LIVE_API_KEY", "ENVLIVEKEY")
    monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "envlivesecret")
    store.save(1, "alpaca_live", {"api_key": "PKLIVECONN", "secret_key": "livesecretconn"})

    client = AlpacaLiveTradingClient(user_id=1)
    assert client.api_key == "PKLIVECONN"
    assert client.secret_key == "livesecretconn"


# ---------------------------------------------------------------------------
# infrastructure/llm/providers -- make_llm_client(user_id=...)
# ---------------------------------------------------------------------------

def test_make_llm_client_prefers_connections_commonstack_key(store, monkeypatch):
    from dashboard.backend.infrastructure.llm import providers

    monkeypatch.delenv("COMMONSTACK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store.save(1, "commonstack", {"api_key": "cs-conn-saved"})

    captured = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(providers, "HAS_ANTHROPIC", True)
    monkeypatch.setattr(providers, "_Anthropic", _FakeAnthropic)

    client = providers.make_llm_client(user_id=1)
    assert client is not None
    assert captured.get("api_key") == "cs-conn-saved"


def test_make_llm_client_without_user_id_is_env_var_only(store, monkeypatch):
    from dashboard.backend.infrastructure.llm import providers

    monkeypatch.delenv("COMMONSTACK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store.save(1, "commonstack", {"api_key": "cs-conn-saved"})

    monkeypatch.setattr(providers, "HAS_ANTHROPIC", True)
    monkeypatch.setattr(providers, "_Anthropic", lambda **kwargs: object())

    # No user_id, no env keys set -> nothing resolves, client is None (falls
    # back to rule-based trading), exactly as before this sub-phase.
    client = providers.make_llm_client()
    assert client is None


def test_resolve_integration_auto_detects_connections_commonstack_key(store, monkeypatch):
    from dashboard.backend.infrastructure.llm import providers

    monkeypatch.delenv("COMMONSTACK_API_KEY", raising=False)
    store.save(1, "commonstack", {"api_key": "cs-conn-saved"})

    assert providers.resolve_integration(None, user_id=1) == "commonstack"
    assert providers.resolve_integration(None, user_id=None) == "anthropic"
