"""POST /v1/agents/upload -- create a built-in agent from an uploaded file
instead of typing a trading instruction by hand. Mirrors
test_agent_starter_defaults.py's isolation pattern; the LLM extraction step
(domain/strategy_extraction.py::extract_agent_prompt) is mocked.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import dashboard.backend.domain.agents.repository as agent_store_module
from dashboard.backend.app import app
from dashboard.backend.domain.agents.defaults import SIMPLE_INSTRUCTION_PRESET_KEY

AgentStore = agent_store_module.AgentStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    import dashboard.backend.api.routers.agents as agents_api
    import dashboard.backend.database as db_module

    db_path = tmp_path / "test.db"
    test_agents = AgentStore(db_path=db_path)
    test_db = db_module.BacktestDatabase(db_path=db_path)
    monkeypatch.setattr(agent_store_module, "agent_store", test_agents)
    monkeypatch.setattr(agents_api.agent_service, "agents", test_agents)
    monkeypatch.setattr(agents_api.agent_service, "db", test_db)
    monkeypatch.setattr(db_module, "db", test_db)
    return TestClient(app)


def _headers():
    return {"X-Session-Id": str(uuid.uuid4())}


def test_upload_creates_an_agent_with_the_extracted_prompt(client, monkeypatch):
    from dashboard.backend.domain.strategy_extraction import AgentPromptExtractionResult

    monkeypatch.setattr(
        "dashboard.backend.domain.strategy_extraction.extract_agent_prompt",
        lambda raw, asset_class, user_id=None: AgentPromptExtractionResult(
            prompt="Buy the dip on major coins and take profit on run-ups.",
            summary="Extracted a crypto dip-buying instruction.",
        ),
    )
    files = {"file": ("strategy.txt", b"buy dips sell rips", "text/plain")}
    resp = client.post(
        "/api/v1/agents/upload", files=files, data={"name": "Uploaded Agent", "asset_class": "crypto"},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    agent = body["agent"]
    assert agent["asset_class"] == "crypto"
    assert len(agent["pipeline"]) == 1
    assert agent["pipeline"][0]["presetKey"] == SIMPLE_INSTRUCTION_PRESET_KEY
    assert agent["pipeline"][0]["prompt"] == "Buy the dip on major coins and take profit on run-ups."
    assert body["extraction_summary"] == "Extracted a crypto dip-buying instruction."


def test_upload_defaults_name_from_filename_when_not_given(client, monkeypatch):
    from dashboard.backend.domain.strategy_extraction import AgentPromptExtractionResult

    monkeypatch.setattr(
        "dashboard.backend.domain.strategy_extraction.extract_agent_prompt",
        lambda raw, asset_class, user_id=None: AgentPromptExtractionResult(prompt="Do something.", summary="ok"),
    )
    files = {"file": ("my_cool_strategy.py", b"some content", "text/plain")}
    resp = client.post("/api/v1/agents/upload", files=files, headers=_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent"]["name"] == "my_cool_strategy"


def test_upload_rejects_inconvertible_file(client, monkeypatch):
    from dashboard.backend.domain.strategy_extraction import AgentPromptExtractionResult

    monkeypatch.setattr(
        "dashboard.backend.domain.strategy_extraction.extract_agent_prompt",
        lambda raw, asset_class, user_id=None: AgentPromptExtractionResult(
            prompt=None, summary="This is a grocery list, not a strategy.",
        ),
    )
    files = {"file": ("grocery.txt", b"milk, eggs, bread", "text/plain")}
    resp = client.post("/api/v1/agents/upload", files=files, headers=_headers())
    assert resp.status_code == 400
    assert "grocery list" in resp.json()["detail"]


def test_upload_rejects_non_utf8_file(client):
    files = {"file": ("bad.bin", b"\xff\xfe\x00\x01", "application/octet-stream")}
    resp = client.post("/api/v1/agents/upload", files=files, headers=_headers())
    assert resp.status_code == 400


def test_upload_rejects_unknown_asset_class(client):
    files = {"file": ("s.txt", b"content", "text/plain")}
    resp = client.post(
        "/api/v1/agents/upload", files=files, data={"asset_class": "dogecoin_futures"}, headers=_headers(),
    )
    assert resp.status_code == 422
