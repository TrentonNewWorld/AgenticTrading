"""Two-layer scan for a Testing-page upload, run before any backtest.

Layer 1 (``domain.manual10.sandbox.validate_code``) is the real security
boundary -- a pure AST allowlist, same as every other code upload path in
this app. It runs first and is authoritative: if it rejects the code, this
module never reaches an LLM at all, and the item is rejected deterministically
and for free.

Layer 2 is an LLM review with two distinct questions, both required by this
feature's spec: (1) does this code actually implement a trading strategy
(read market data, decide a position), or is it something else entirely
smuggled in under the allowlist -- and (2) is there anything the AST check
can't catch (obfuscated intent, logic that doesn't look like real trading
logic). Like every LLM review elsewhere in this codebase, it is a heuristic
on top of the sandbox, never a substitute for it -- see sandbox.py's
docstring for the full three-layer rationale this scanner sits on top of.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from dashboard.backend.domain.manual10.sandbox import StrategyCodeError, validate_code
from dashboard.backend.infrastructure.llm.backtest_harness import (
    HAS_ANTHROPIC,
    extract_response_text,
    make_llm_client,
)

_REVIEW_SYSTEM_PROMPT = """You are reviewing a Python function submitted to a trading-strategy testing \
pipeline. The code already passed a strict static allowlist (only math/statistics/json/datetime imports, \
no eval/exec/open/os/subprocess/socket, no dunder attribute access) and will run in an isolated subprocess \
with no credentials, no network, and a hard timeout -- so your review is a second, independent judgment call, \
not the security boundary itself.

The function is named decide(price_history), where price_history is {symbol: [{"t": date, "close": price}, ...]}, \
and it must return {symbol: weight} representing a target portfolio allocation.

Answer exactly two questions, then two required tag lines:
1. Does this code genuinely implement a trading strategy -- does it read the price data and compute an \
allocation decision from it -- or does it do something else (a no-op, unrelated computation, an attempt to \
smuggle something past the allowlist, obfuscated/unreadable logic with no discernible trading rationale)?
2. Is there anything suspicious a mechanical check might miss (obfuscation, control flow that looks designed \
to evade review, anything that doesn't look like a real trading decision)?

Write 2-4 sentences covering both, then end with exactly these two lines:
"IS_STRATEGY: yes" or "IS_STRATEGY: no"
"RISK: low" or "RISK: medium" or "RISK: high\""""

_OPTIONS_REVIEW_SYSTEM_PROMPT = """You are reviewing a Python function submitted to an OPTIONS \
trading-strategy testing pipeline. The code already passed a strict static allowlist (only math/statistics/\
json/datetime imports, no eval/exec/open/os/subprocess/socket, no dunder attribute access) and will run in \
an isolated subprocess with no credentials, no network, and a hard timeout -- so your review is a second, \
independent judgment call, not the security boundary itself.

The function is named decide_options(as_of, positions, chain, account), where `chain` is \
{underlying: [{"symbol": occ_contract_symbol, "strike": float, "expiration": date, "right": "C"|"P", \
"bid": float, "ask": float, "last": float, "open_interest": int}, ...]} and `positions` is the strategy's \
own currently-held legs in the same shape. It must return a list of order intents: \
[{"action": "open"|"close", "symbol": occ_contract_symbol, "side": "buy"|"sell", "qty": int, \
"leg_role": "option"|"stock"|"single"}, ...] -- picking exact contracts, strikes, and expirations itself.

Answer exactly two questions, then two required tag lines:
1. Does this code genuinely implement an options trading strategy -- does it read the chain/positions data \
and compute contract-level order intents from it -- or does it do something else (a no-op, unrelated \
computation, an attempt to smuggle something past the allowlist, obfuscated/unreadable logic with no \
discernible options-trading rationale)?
2. Is there anything suspicious a mechanical check might miss (obfuscation, control flow that looks designed \
to evade review, anything that doesn't look like a real options trading decision)?

Write 2-4 sentences covering both, then end with exactly these two lines:
"IS_STRATEGY: yes" or "IS_STRATEGY: no"
"RISK: low" or "RISK: medium" or "RISK: high\""""

_FUTURES_REVIEW_SYSTEM_PROMPT = """You are reviewing a Python function submitted to a FUTURES \
trading-strategy testing pipeline. The code already passed a strict static allowlist (only math/statistics/\
json/datetime imports, no eval/exec/open/os/subprocess/socket, no dunder attribute access) and will run in \
an isolated subprocess with no credentials, no network, and a hard timeout -- so your review is a second, \
independent judgment call, not the security boundary itself.

The function is named decide_futures(as_of, positions, quotes, account), where `quotes` is \
{symbol: {"price": float, "prev_close": float}} for a small universe of futures continuous-contract symbols \
(e.g. "ES=F", "GC=F") and `positions` is the strategy's own currently-held positions, each {"symbol", "qty"}. \
It must return a list of order intents: [{"action": "open"|"close", "symbol": str, "side": "buy"|"sell", \
"qty": int}, ...].

Answer exactly two questions, then two required tag lines:
1. Does this code genuinely implement a futures trading strategy -- does it read the quotes/positions data \
and compute order intents from it -- or does it do something else (a no-op, unrelated computation, an attempt \
to smuggle something past the allowlist, obfuscated/unreadable logic with no discernible trading rationale)?
2. Is there anything suspicious a mechanical check might miss (obfuscation, control flow that looks designed \
to evade review, anything that doesn't look like a real futures trading decision)?

Write 2-4 sentences covering both, then end with exactly these two lines:
"IS_STRATEGY: yes" or "IS_STRATEGY: no"
"RISK: low" or "RISK: medium" or "RISK: high\""""


_FOREX_REVIEW_SYSTEM_PROMPT = """You are reviewing a Python function submitted to a FOREX \
trading-strategy testing pipeline. The code already passed a strict static allowlist (only math/statistics/\
json/datetime imports, no eval/exec/open/os/subprocess/socket, no dunder attribute access) and will run in \
an isolated subprocess with no credentials, no network, and a hard timeout -- so your review is a second, \
independent judgment call, not the security boundary itself.

The function is named decide_forex(as_of, positions, quotes, account), where `quotes` is \
{symbol: {"price": float, "prev_close": float}} for a small universe of major USD-quote forex pairs \
(e.g. "EURUSD=X", "GBPUSD=X") and `positions` is the strategy's own currently-held positions, each \
{"symbol", "qty"}. It must return a list of order intents: [{"action": "open"|"close", "symbol": str, \
"side": "buy"|"sell", "qty": int}, ...].

Answer exactly two questions, then two required tag lines:
1. Does this code genuinely implement a forex trading strategy -- does it read the quotes/positions data \
and compute order intents from it -- or does it do something else (a no-op, unrelated computation, an attempt \
to smuggle something past the allowlist, obfuscated/unreadable logic with no discernible trading rationale)?
2. Is there anything suspicious a mechanical check might miss (obfuscation, control flow that looks designed \
to evade review, anything that doesn't look like a real forex trading decision)?

Write 2-4 sentences covering both, then end with exactly these two lines:
"IS_STRATEGY: yes" or "IS_STRATEGY: no"
"RISK: low" or "RISK: medium" or "RISK: high\""""


_CRYPTO_REVIEW_SYSTEM_PROMPT = """You are reviewing a Python function submitted to a CRYPTO \
trading-strategy testing pipeline. The code already passed a strict static allowlist (only math/statistics/\
json/datetime imports, no eval/exec/open/os/subprocess/socket, no dunder attribute access) and will run in \
an isolated subprocess with no credentials, no network, and a hard timeout -- so your review is a second, \
independent judgment call, not the security boundary itself.

The function is named decide_crypto(as_of, positions, quotes, account), where `quotes` is \
{symbol: {"price": float, "prev_close": float}} for a small universe of major USD-quote crypto pairs \
(e.g. "BTC/USD", "ETH/USD") and `positions` is the strategy's own currently-held positions, each \
{"symbol", "qty"} where qty may be a fraction of one coin. It must return a list of order intents: \
[{"action": "open"|"close", "symbol": str, "side": "buy"|"sell", "qty": float}, ...].

Answer exactly two questions, then two required tag lines:
1. Does this code genuinely implement a crypto trading strategy -- does it read the quotes/positions data \
and compute order intents from it -- or does it do something else (a no-op, unrelated computation, an attempt \
to smuggle something past the allowlist, obfuscated/unreadable logic with no discernible trading rationale)?
2. Is there anything suspicious a mechanical check might miss (obfuscation, control flow that looks designed \
to evade review, anything that doesn't look like a real crypto trading decision)?

Write 2-4 sentences covering both, then end with exactly these two lines:
"IS_STRATEGY: yes" or "IS_STRATEGY: no"
"RISK: low" or "RISK: medium" or "RISK: high\""""


def _parse_tag(text: str, tag: str, allowed: set[str]) -> str:
    match = re.search(rf"{tag}:\s*(\w+)", text, re.IGNORECASE)
    value = (match.group(1).lower() if match else "")
    return value if value in allowed else "unknown"


def scan(code: str, asset_class: str = "stocks", user_id: Optional[int] = None) -> Dict[str, Any]:
    """Returns {accepted, verdict, notes, is_strategy}. Never raises -- a
    scan failure (no LLM configured, an API error) degrades to an 'unknown'
    verdict with a plain explanation, exactly like every other LLM-review
    call site in this app; only the AST layer can hard-reject.

    ``asset_class="options"``/``"futures"`` switch the AST check to that
    asset class's own sandbox (requires decide_options/decide_futures, not
    decide) and the review prompt to describe that contract shape --
    everything else about the two-layer scan is identical."""
    if asset_class == "options":
        from dashboard.backend.domain.options.sandbox import (
            OptionsStrategyCodeError, validate_code as validate_options_code,
        )
        try:
            validate_options_code(code)
        except OptionsStrategyCodeError as exc:
            return {
                "accepted": False,
                "verdict": "rejected",
                "notes": f"Rejected by static analysis (this is the real security check, not a heuristic): {exc}",
                "is_strategy": False,
            }
    elif asset_class == "futures":
        from dashboard.backend.domain.futures.sandbox import (
            FuturesStrategyCodeError, validate_code as validate_futures_code,
        )
        try:
            validate_futures_code(code)
        except FuturesStrategyCodeError as exc:
            return {
                "accepted": False,
                "verdict": "rejected",
                "notes": f"Rejected by static analysis (this is the real security check, not a heuristic): {exc}",
                "is_strategy": False,
            }
    elif asset_class == "forex":
        from dashboard.backend.domain.forex.sandbox import (
            ForexStrategyCodeError, validate_code as validate_forex_code,
        )
        try:
            validate_forex_code(code)
        except ForexStrategyCodeError as exc:
            return {
                "accepted": False,
                "verdict": "rejected",
                "notes": f"Rejected by static analysis (this is the real security check, not a heuristic): {exc}",
                "is_strategy": False,
            }
    elif asset_class == "crypto":
        from dashboard.backend.domain.crypto.sandbox import (
            CryptoStrategyCodeError, validate_code as validate_crypto_code,
        )
        try:
            validate_crypto_code(code)
        except CryptoStrategyCodeError as exc:
            return {
                "accepted": False,
                "verdict": "rejected",
                "notes": f"Rejected by static analysis (this is the real security check, not a heuristic): {exc}",
                "is_strategy": False,
            }
    else:
        try:
            validate_code(code)
        except StrategyCodeError as exc:
            return {
                "accepted": False,
                "verdict": "rejected",
                "notes": f"Rejected by static analysis (this is the real security check, not a heuristic): {exc}",
                "is_strategy": False,
            }

    if not HAS_ANTHROPIC:
        return {
            "accepted": True,
            "verdict": "unknown",
            "notes": "Static analysis passed. No LLM available in this environment -- the second-opinion "
                     "review (malware/intent + 'is this really a strategy') was not performed.",
            "is_strategy": True,  # AST already requires a decide() function; treat as strategy-shaped
        }
    client = make_llm_client(user_id=user_id)
    if client is None:
        return {
            "accepted": True,
            "verdict": "unknown",
            "notes": "Static analysis passed. No LLM API key configured -- the second-opinion review was "
                     "not performed.",
            "is_strategy": True,
        }

    if asset_class == "options":
        system_prompt = _OPTIONS_REVIEW_SYSTEM_PROMPT
    elif asset_class == "futures":
        system_prompt = _FUTURES_REVIEW_SYSTEM_PROMPT
    elif asset_class == "forex":
        system_prompt = _FOREX_REVIEW_SYSTEM_PROMPT
    elif asset_class == "crypto":
        system_prompt = _CRYPTO_REVIEW_SYSTEM_PROMPT
    else:
        system_prompt = _REVIEW_SYSTEM_PROMPT
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=350,
            system=system_prompt,
            messages=[{"role": "user", "content": f"```python\n{code}\n```"}],
        )
        text = extract_response_text(response).strip()
    except Exception as exc:  # noqa: BLE001 -- any provider/network failure degrades, never raises
        return {
            "accepted": True,
            "verdict": "unknown",
            "notes": f"Static analysis passed. Review call failed: {exc}",
            "is_strategy": True,
        }

    risk = _parse_tag(text, "RISK", {"low", "medium", "high"})
    is_strategy = _parse_tag(text, "IS_STRATEGY", {"yes", "no"}) != "no"
    accepted = is_strategy and risk != "high"
    verdict = risk if risk != "unknown" else "unknown"
    if not is_strategy:
        verdict = "not_a_strategy"
    return {
        "accepted": accepted,
        "verdict": verdict,
        "notes": text,
        "is_strategy": is_strategy,
    }
