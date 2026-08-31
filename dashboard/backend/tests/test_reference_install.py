"""Reference-package install: the sellable strategy packs export built-in
registry strategies as `*-strategy-reference-v1` files (the Python class ships
with every bot; only the roster entry travels). A blank-slate install must be
able to upload those files on the Testing page and get a working catalog entry
-- before this existed, every file in a purchased pack was rejected (found
testing the buyer flow, 2026-08-30).

Deliberately roster-independent: runs identically on a blank-slate clone and
on a configured operator install (it writes to a tmp roster either way).
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.domain.leaderboard import catalog as catalog_module
from dashboard.backend.domain.strategy_testing import promote


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _tmp_roster(tmp_path, monkeypatch):
    roster = tmp_path / "leaderboard.json"
    roster.write_text(json.dumps({"strategies": []}), encoding="utf-8")
    monkeypatch.setattr(catalog_module, "LEADERBOARD_CONFIG_PATH", roster)
    monkeypatch.setattr(catalog_module, "CACHE_PATH", tmp_path / "catalog_cache.json")
    yield roster


def _reference_pkg(fmt="newworldtrading-strategy-reference-v1"):
    return {
        "format": fmt,
        "executable": False,
        "name": "12-Month Momentum",
        "description": "Ranks all 30 Dow stocks by trailing 12-month return.",
        "catalog_entry": {
            "id": "momentum_effect",
            "name": "NewWorldTrading",
            "label": "Baseline Strategy",
            "model": "12-Month Momentum",
            "strategy": "momentum_effect",
        },
        "parameter_schema": {},
        "parameter_values": {},
    }


def test_install_registers_entry_into_empty_roster(_tmp_roster):
    entry = promote.install_reference_package(_reference_pkg())
    assert entry["id"] == "momentum_effect"
    saved = json.loads(_tmp_roster.read_text(encoding="utf-8"))
    assert [s["id"] for s in saved["strategies"]] == ["momentum_effect"]
    assert saved["strategies"][0]["source"] == "Installed Pack"


def test_install_accepts_the_pre_rebrand_format_string(client):
    pkg = _reference_pkg(fmt="agentic-trading-lab-strategy-reference-v1")
    resp = client.post(
        "/api/v1/strategy-testing/submit",
        files={"file": ("m.strategy.json", io.BytesIO(json.dumps(pkg).encode()), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reference_installed"] is True
    assert body["name"] == "12-Month Momentum"


def test_install_rejects_a_reference_with_no_entry(client):
    pkg = _reference_pkg()
    del pkg["catalog_entry"]
    resp = client.post(
        "/api/v1/strategy-testing/submit",
        files={"file": ("m.strategy.json", io.BytesIO(json.dumps(pkg).encode()), "application/json")},
    )
    assert resp.status_code == 400
    assert "no installable catalog entry" in resp.json()["detail"]


def test_install_rejects_unknown_strategy_type():
    pkg = _reference_pkg()
    pkg["catalog_entry"]["strategy"] = "not_a_real_strategy_type"
    with pytest.raises(Exception):
        promote.install_reference_package(pkg)


def test_duplicate_install_gets_a_unique_id(_tmp_roster):
    promote.install_reference_package(_reference_pkg())
    entry2 = promote.install_reference_package(_reference_pkg())
    saved = json.loads(_tmp_roster.read_text(encoding="utf-8"))
    ids = [s["id"] for s in saved["strategies"]]
    assert len(ids) == len(set(ids)) == 2
    assert entry2["id"] != "momentum_effect"


def test_export_now_carries_the_install_payload(_tmp_roster, monkeypatch):
    roster = {"strategies": [{
        "id": "momentum_effect", "name": "NewWorldTrading", "label": "Baseline Strategy",
        "model": "12-Month Momentum", "strategy": "momentum_effect",
    }]}
    _tmp_roster.write_text(json.dumps(roster), encoding="utf-8")
    pkg = catalog_module.build_export("momentum_effect")
    assert pkg["format"].endswith("-strategy-reference-v1")
    assert pkg["name"] == "12-Month Momentum"  # the display name, not the team name
    assert pkg["catalog_entry"]["strategy"] == "momentum_effect"
    # round-trip: the export installs into a fresh roster
    _tmp_roster.write_text(json.dumps({"strategies": []}), encoding="utf-8")
    entry = promote.install_reference_package(pkg)
    assert entry["id"] == "momentum_effect"
