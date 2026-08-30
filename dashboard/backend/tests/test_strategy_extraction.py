"""Tests for domain/strategy_extraction.py -- the shared "make sense of this
upload" step used by every asset class's Testing/upload path and My Agents'
upload-a-file path. The LLM is mocked throughout; what's under test is the
module's own logic (already-valid short-circuit, re-validating the LLM's
output, degrading gracefully when no LLM is available).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from dashboard.backend.domain.strategy_extraction import extract_strategy_code

VALID_CRYPTO_CODE = "def decide_crypto(as_of, positions, quotes, account):\n    return []\n"
GIBBERISH = "this is not python or a strategy at all, just some notes"


class _FakeMessages:
    def __init__(self, reply_text):
        self.reply_text = reply_text
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply_text)])


class _FakeClient:
    def __init__(self, reply_text):
        self.messages = _FakeMessages(reply_text)


def test_already_valid_code_short_circuits_with_no_llm_call(monkeypatch):
    # If the LLM were called, this would blow up (no client registered) --
    # the absence of an error IS the assertion that it short-circuited.
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    result = extract_strategy_code(VALID_CRYPTO_CODE, "crypto")
    assert result.code == VALID_CRYPTO_CODE.strip()
    assert result.was_already_valid is True


def test_empty_upload_is_rejected_without_calling_the_llm():
    result = extract_strategy_code("   ", "crypto")
    assert result.code is None
    assert "empty" in result.summary


def test_unknown_asset_class_is_rejected():
    result = extract_strategy_code(VALID_CRYPTO_CODE, "not_a_real_asset_class")
    assert result.code is None
    assert "unknown asset class" in result.summary


def test_llm_converts_prose_into_valid_code(monkeypatch):
    reply = json.dumps({
        "convertible": True,
        "code": VALID_CRYPTO_CODE,
        "summary": "Wrote a do-nothing placeholder from your description.",
    })
    fake_client = _FakeClient(reply)
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: fake_client,
    )
    result = extract_strategy_code("buy dips, sell rips, described in English", "crypto")
    assert result.code == VALID_CRYPTO_CODE
    assert result.was_already_valid is False
    assert "placeholder" in result.summary
    assert fake_client.messages.calls == 1


def test_llm_says_not_convertible(monkeypatch):
    reply = json.dumps({"convertible": False, "code": None, "summary": "This is a grocery list, not a strategy."})
    fake_client = _FakeClient(reply)
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: fake_client,
    )
    result = extract_strategy_code(GIBBERISH, "crypto")
    assert result.code is None
    assert "grocery list" in result.summary


def test_llm_output_that_still_fails_validation_is_rejected_not_trusted(monkeypatch):
    # The LLM claims success but hands back code missing the required
    # function -- this must be caught by re-validation, never executed.
    reply = json.dumps({"convertible": True, "code": "def wrong_function_name():\n    pass\n", "summary": "done"})
    fake_client = _FakeClient(reply)
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: fake_client,
    )
    result = extract_strategy_code(GIBBERISH, "crypto")
    assert result.code is None
    assert "safety validation" in result.summary


def test_malformed_llm_json_degrades_to_a_summary_not_a_crash(monkeypatch):
    fake_client = _FakeClient("not json at all")
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: fake_client,
    )
    result = extract_strategy_code(GIBBERISH, "crypto")
    assert result.code is None
    assert "Conversion attempt failed" in result.summary


def test_no_llm_client_available_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: None,
    )
    result = extract_strategy_code(GIBBERISH, "crypto")
    assert result.code is None
    assert "No LLM API key" in result.summary


def test_works_for_every_registered_asset_class(monkeypatch):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not be called for already-valid code")),
    )
    valid_by_class = {
        "stocks": "def decide(price_history):\n    return {}\n",
        "options": "def decide_options(as_of, positions, chain, account):\n    return []\n",
        "futures": "def decide_futures(as_of, positions, quotes, account):\n    return []\n",
        "forex": "def decide_forex(as_of, positions, quotes, account):\n    return []\n",
        "crypto": VALID_CRYPTO_CODE,
        "prediction": "def decide_prediction(as_of, positions, markets, account):\n    return []\n",
    }
    for asset_class, code in valid_by_class.items():
        result = extract_strategy_code(code, asset_class)
        assert result.was_already_valid, f"{asset_class} should have recognized its own valid code"
