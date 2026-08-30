"""POST /api/v1/agents/{agent_id}/convert-to-strategy.

Turns an agent's pipeline trading instruction into a permanent Strategy
Catalog entry (via the ``llm_agent`` strategy), then deletes the source
agent -- it no longer exists afterward, but the dashboard now knows how to
trade like it through the new catalog entry.
"""

import json
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.domain.agents.credential_store import AgentCredentialStore
from dashboard.backend.domain.leaderboard import catalog as catalog_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    import dashboard.backend.domain.agents.repository as agent_store_module
    import dashboard.backend.api.routers.agents as agents_api
    import dashboard.backend.domain.agents.service as agent_service_module
    import dashboard.backend.database as db_module
    import dashboard.backend.domain.brokers.repository as broker_repository

    db_path = tmp_path / "test.db"
    test_agents = agent_store_module.AgentStore(db_path=db_path)
    test_credentials = AgentCredentialStore(db_path=db_path)
    test_db = db_module.BacktestDatabase(db_path=db_path)
    monkeypatch.setenv(broker_repository._KEY_ENV_VAR, Fernet.generate_key().decode())
    monkeypatch.setattr(broker_repository, "_fernet_instance", None)
    monkeypatch.setattr(agent_store_module, "agent_store", test_agents)
    monkeypatch.setattr(agents_api.agent_service, "agents", test_agents)
    monkeypatch.setattr(agents_api.agent_service, "db", test_db)
    monkeypatch.setattr(agents_api, "agent_credential_store", test_credentials)
    monkeypatch.setattr(agent_service_module, "agent_credential_store", test_credentials)
    monkeypatch.setattr(db_module, "db", test_db)
    agents_api._credential_rate_limiter.reset()

    # Isolate leaderboard.json so the test never touches the real committed
    # config, and stub out the background post-conversion refresh -- it would
    # otherwise make real LLM API calls in a test.
    config_path = tmp_path / "leaderboard.json"
    config_path.write_text(json.dumps({"strategies": []}), encoding="utf-8")
    monkeypatch.setattr(catalog_module, "LEADERBOARD_CONFIG_PATH", config_path)
    # The route fires a background thread that calls get_strategy_catalog --
    # stub that call rather than the global threading.Thread class (patching
    # threading.Thread process-wide breaks anyio's own worker-thread use
    # under TestClient). The thread still spawns; it just does nothing.
    monkeypatch.setattr(catalog_module, "get_strategy_catalog", lambda **kwargs: None)

    return TestClient(app)


def _create_builtin_with_prompt(client, headers, prompt="Buy only what you would hold for a week."):
    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Weekly Holder",
            "model_name": "anthropic/claude-haiku-4-5",
            "agent_type": "builtin",
            "description": "The original.",
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    agent = created.json()["agent"]
    pipeline = [
        {
            "id": "sub_custom",
            "presetKey": "simple_instruction",
            "label": "Trading instruction",
            "prompt": prompt,
            "outputFormat": "JSON: { \"orders\": [] }",
        }
    ]
    patched = client.patch(
        f"/api/v1/agents/{agent['agent_id']}", json={"pipeline": pipeline}, headers=headers,
    )
    assert patched.status_code == 200, patched.text
    return agent["agent_id"]


def test_convert_writes_a_catalog_entry_and_deletes_the_agent(client):
    headers = {"X-Session-Id": str(uuid.uuid4())}
    agent_id = _create_builtin_with_prompt(client, headers)

    resp = client.post(f"/api/v1/agents/{agent_id}/convert-to-strategy", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["converted"] is True
    assert body["deleted_agent_id"] == agent_id

    entry = body["strategy"]
    assert entry["strategy"] == "llm_agent"
    assert entry["label"] == "Baseline Strategy"
    assert entry["model_id"] == "anthropic/claude-haiku-4-5"
    assert "Weekly Holder" in entry["description"]
    assert "hold for a week" in entry["description"]

    # The agent is really gone.
    fetched = client.get(f"/api/v1/agents/{agent_id}", headers=headers)
    assert fetched.status_code == 404

    # And the new entry actually resolves as a tradeable catalog roster member.
    roster_keys = [e.key for e in catalog_module._catalog_roster()]
    assert entry["id"] in roster_keys


def test_convert_rejects_an_agent_with_no_pipeline_prompt(client):
    headers = {"X-Session-Id": str(uuid.uuid4())}
    created = client.post(
        "/api/v1/agents",
        json={"name": "Blank", "model_name": "anthropic/claude-haiku-4-5", "agent_type": "builtin"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["agent"]["agent_id"]
    # Wipe out whatever starter pipeline was seeded.
    client.patch(f"/api/v1/agents/{agent_id}", json={"pipeline": []}, headers=headers)

    resp = client.post(f"/api/v1/agents/{agent_id}/convert-to-strategy", headers=headers)
    assert resp.status_code == 400

    # Rejected before deleting anything.
    assert client.get(f"/api/v1/agents/{agent_id}", headers=headers).status_code == 200


def test_convert_rejects_non_pipeline_runtime(client):
    headers = {"X-Session-Id": str(uuid.uuid4())}
    created = client.post(
        "/api/v1/agents",
        json={
            "name": "Hedge Fund Runtime",
            "model_name": "anthropic/claude-haiku-4-5",
            "agent_type": "builtin",
            "runtime_type": "ai_hedge_fund",
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["agent"]["agent_id"]

    resp = client.post(f"/api/v1/agents/{agent_id}/convert-to-strategy", headers=headers)
    assert resp.status_code == 400
    assert client.get(f"/api/v1/agents/{agent_id}", headers=headers).status_code == 200


def test_convert_produces_a_unique_id_on_name_collision(client):
    headers = {"X-Session-Id": str(uuid.uuid4())}
    first_id = _create_builtin_with_prompt(client, headers, prompt="First instruction.")
    resp1 = client.post(f"/api/v1/agents/{first_id}/convert-to-strategy", headers=headers)
    assert resp1.status_code == 200, resp1.text

    second_id = _create_builtin_with_prompt(client, headers, prompt="Second instruction.")
    resp2 = client.post(f"/api/v1/agents/{second_id}/convert-to-strategy", headers=headers)
    assert resp2.status_code == 200, resp2.text

    assert resp1.json()["strategy"]["id"] != resp2.json()["strategy"]["id"]
