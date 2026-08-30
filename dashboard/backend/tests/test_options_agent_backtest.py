"""run_llm_agent_backtest: the LLM-driven counterpart to run_backtest for
options, used by My Agents options agents instead of sandboxed code
(api/routers/backtests.py's run_non_stocks_agent_backtest_background
dispatches here). Same day-by-day loop, same expiration-settlement rules
(already pinned by test_options_backtester.py) -- these tests only cover
what's actually new: the LLM decision step and its fallbacks, mirroring
test_crypto_agent_backtest.py's shape for the crypto case.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from dashboard.backend.domain.options import backtester
from dashboard.backend.domain.options.backtester import run_llm_agent_backtest
from dashboard.backend.domain.options.contracts import CandidateContract

DAY1 = date(2026, 1, 5)
DAY2 = date(2026, 1, 6)
DAY3 = date(2026, 1, 7)
SYMBOL = "XYZ260107C00100000"


def _bars_frame(rows):
    frame = pd.DataFrame(rows)
    frame["t"] = pd.to_datetime(frame["t"])
    return frame.set_index("t")


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


@pytest.fixture
def stub_candidates(monkeypatch):
    candidate = CandidateContract(
        symbol=SYMBOL, underlying="XYZ", expiration=DAY3, right="C", strike=100.0, bar_count=2,
    )
    monkeypatch.setattr(backtester, "find_candidate_contracts", lambda *a, **k: [candidate])
    bars = {SYMBOL: _bars_frame([
        {"t": "2026-01-05", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 10},
        {"t": "2026-01-06", "open": 6.0, "high": 6.0, "low": 6.0, "close": 6.0, "volume": 8},
    ])}
    monkeypatch.setattr(backtester, "get_option_bars", lambda *a, **k: bars)
    monkeypatch.setattr(
        backtester, "_fetch_underlying_daily_closes",
        lambda *a, **k: {DAY1: 95.0, DAY2: 96.0, DAY3: 110.0},
    )
    return candidate


def test_no_llm_client_returns_empty_curve(monkeypatch, stub_candidates):
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: None,
    )
    curve = run_llm_agent_backtest("buy calls", None, ["XYZ"], DAY1, DAY3, 1000.0)
    assert curve == []


def test_llm_picks_the_contract_symbol_from_the_chain(monkeypatch, stub_candidates):
    # The fake client "reads" the chain and echoes back the one contract's
    # OCC symbol -- exactly the reuse the module docstring describes: the LLM
    # never invents strike/expiration/right, it just picks a symbol it was shown.
    fake_client = _FakeClient(
        f'[{{"action": "open", "symbol": "{SYMBOL}", "side": "buy", "qty": 1, "leg_role": "single"}}]'
    )
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: fake_client,
    )
    curve = run_llm_agent_backtest("buy the first call and hold to expiration", None, ["XYZ"], DAY1, DAY3, 1000.0)
    # The fake client is a canned reply, not a real decision loop, so it
    # reissues the same "open, buy" intent on day2 too -- options' cash-
    # sufficiency cap (matching futures/forex/crypto, added after this test
    # first caught its absence) applies to "buy" opens, so day2's second
    # $600 buy is refused against the $500 cash left after day1's: only one
    # leg ever exists, bought and marked at $5 on day1, marked at $6 on
    # day2, settling ITM for $1000 on day3.
    assert curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)  # 1 leg, bought and marked at $5 same day
    assert curve[1]["equity"] == pytest.approx(1100.0, abs=1e-6)  # cash $500 + 1 leg marked at $6 = $600
    assert curve[2]["equity"] == pytest.approx(1500.0, abs=1e-6)  # cash $500 + settlement 1*10*100 = $1000
    # Only 2 calls, not 3: day3 (expiration) has no option bar of its own
    # (the fixture's bars stop at day2), so that day's chain is empty and the
    # decision step -- LLM or sandboxed code alike -- is never invoked, same
    # as test_options_backtester.py's equivalent case.
    assert fake_client.messages.calls == 2


def test_malformed_llm_reply_degrades_to_no_orders_not_a_crash(monkeypatch, stub_candidates):
    fake_client = _FakeClient("not json at all")
    monkeypatch.setattr(
        "dashboard.backend.infrastructure.llm.providers.make_llm_client", lambda **kwargs: fake_client,
    )
    curve = run_llm_agent_backtest("anything", None, ["XYZ"], DAY1, DAY3, 1000.0)
    assert [p["equity"] for p in curve] == [1000.0, 1000.0, 1000.0]
