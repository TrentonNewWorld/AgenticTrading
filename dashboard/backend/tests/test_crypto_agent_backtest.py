"""run_llm_agent_backtest: the LLM-driven counterpart to run_backtest, used by
My Agents crypto agents instead of sandboxed code (api/routers/backtests.py's
run_non_stocks_agent_backtest_background dispatches here). Same day-by-day
loop, same cash-cap/mark-to-market rules (already pinned by
test_crypto_backtester.py) -- these tests only cover what's actually new:
the LLM decision step and its fallbacks.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import dashboard.backend.domain.crypto.backtester as backtester_module
from dashboard.backend.domain.crypto.backtester import run_llm_agent_backtest

BARS = {
    "BTC/USD": [
        {"t": "2026-08-01", "o": 77000, "h": 77200, "l": 76800, "c": 77000, "v": 0},
        {"t": "2026-08-02", "o": 77000, "h": 78200, "l": 76900, "c": 78000, "v": 0},
        {"t": "2026-08-03", "o": 78000, "h": 79200, "l": 77800, "c": 79000, "v": 0},
    ],
}


def _fake_daily_bars(symbol, start, end):
    return BARS.get(symbol, [])


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


def test_no_llm_client_returns_empty_curve(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", _fake_daily_bars)
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: None,
    )
    curve = run_llm_agent_backtest(
        "buy the dip", None, ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 4), 1000.0,
    )
    assert curve == []


def test_llm_intents_are_applied_through_the_same_cash_cap_loop(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", _fake_daily_bars)
    fake_client = _FakeClient('[{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.01}]')
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: fake_client,
    )
    curve = run_llm_agent_backtest(
        "buy 0.01 BTC on the first day and hold", "claude-haiku-4-5-20251001",
        ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 4), 1000.0,
    )
    # Every day re-issues the same "open" intent (the fake client is a canned
    # reply, not a real decision loop) -- the cash cap is what actually stops
    # it from opening a fresh position every day, so the curve should track
    # holding the FIRST day's 0.01 BTC forward, same shape as the sandboxed
    # equivalent in test_crypto_backtester.py.
    assert curve[0]["equity"] == 1000.0
    assert curve[1]["equity"] > 1000.0  # BTC rose from 77000 to 78000
    assert fake_client.messages.calls == 3


def test_malformed_llm_reply_degrades_to_no_orders_not_a_crash(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", _fake_daily_bars)
    fake_client = _FakeClient("not json at all")
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: fake_client,
    )
    curve = run_llm_agent_backtest(
        "anything", None, ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 4), 1000.0,
    )
    assert [p["equity"] for p in curve] == [1000.0, 1000.0, 1000.0]


def test_markdown_fenced_json_is_still_parsed(monkeypatch):
    monkeypatch.setattr(backtester_module, "get_crypto_daily_bars", _fake_daily_bars)
    fake_client = _FakeClient('```json\n[{"action": "open", "symbol": "BTC/USD", "side": "buy", "qty": 0.01}]\n```')
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: fake_client,
    )
    curve = run_llm_agent_backtest(
        "buy 0.01 BTC", None, ["BTC/USD"], date(2026, 8, 1), date(2026, 8, 4), 1000.0,
    )
    assert curve[1]["equity"] > 1000.0
