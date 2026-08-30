"""Shared "make sense of this upload" step for every asset class's Testing/
upload path, and for My Agents' upload-a-file-to-create-an-agent path
(domain/agents -- see that package's docstring for the upload flow this
feeds).

Before this module existed, an uploaded file had to already be *exact*
sandboxed-code (or, for an agent, there was no upload path at all): a
strategy pasted from a blog post, a screenshot's transcribed logic, a
strategy described in plain English, or code shaped for a different asset
class would all be rejected outright by the AST allowlist with no attempt to
help. This module asks an LLM to either recognize the input as already
valid, or rewrite it into the target asset class's real ``decide_*()``
contract -- but the LLM's output is NEVER trusted directly: it is re-run
through that asset class's own ``validate_code`` (the real security
boundary, an AST allowlist) before being accepted, exactly like every other
code path in this repo that touches an LLM-written string. A rewrite that
doesn't pass validation is reported as "could not convert," never silently
executed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Dict, NamedTuple, Optional


class _AssetClassSpec(NamedTuple):
    validate_code: Callable[[str], None]
    code_error: type
    entry_function: str
    contract_description: str


def _specs() -> Dict[str, _AssetClassSpec]:
    """Built lazily (not at module import) so importing this module doesn't
    eagerly import all 6 asset classes' sandbox modules just to read a
    docstring or call one function -- most callers only need one entry."""
    from dashboard.backend.domain.manual10.sandbox import StrategyCodeError, validate_code as validate_stocks
    from dashboard.backend.domain.options.sandbox import OptionsStrategyCodeError, validate_code as validate_options
    from dashboard.backend.domain.futures.sandbox import FuturesStrategyCodeError, validate_code as validate_futures
    from dashboard.backend.domain.forex.sandbox import ForexStrategyCodeError, validate_code as validate_forex
    from dashboard.backend.domain.crypto.sandbox import CryptoStrategyCodeError, validate_code as validate_crypto
    from dashboard.backend.domain.prediction.sandbox import (
        PredictionStrategyCodeError, validate_code as validate_prediction,
    )

    return {
        "stocks": _AssetClassSpec(
            validate_stocks, StrategyCodeError, "decide",
            "def decide(price_history) -> {symbol: weight, ...} -- price_history is a DailyHistory "
            "of OHLCV bars; return target portfolio weights (0-1, summing to at most 1).",
        ),
        "options": _AssetClassSpec(
            validate_options, OptionsStrategyCodeError, "decide_options",
            "def decide_options(as_of, positions, chain, account) -> [order_intent, ...] -- chain is "
            "{underlying: [{symbol (OCC), strike, expiration, right, bid, ask, last}, ...]}; each "
            'order_intent is {"action": "open"|"close", "symbol", "side": "buy"|"sell", "qty", "leg_role"}.',
        ),
        "futures": _AssetClassSpec(
            validate_futures, FuturesStrategyCodeError, "decide_futures",
            "def decide_futures(as_of, positions, quotes, account) -> [order_intent, ...] -- quotes is "
            '{symbol: {price, prev_close}}; each order_intent is {"action": "open"|"close", "symbol", '
            '"side": "buy"|"sell", "qty" (whole contracts)}.',
        ),
        "forex": _AssetClassSpec(
            validate_forex, ForexStrategyCodeError, "decide_forex",
            "def decide_forex(as_of, positions, quotes, account) -> [order_intent, ...] -- same shape as "
            "futures, qty is whole units.",
        ),
        "crypto": _AssetClassSpec(
            validate_crypto, CryptoStrategyCodeError, "decide_crypto",
            "def decide_crypto(as_of, positions, quotes, account) -> [order_intent, ...] -- same shape as "
            "futures, qty is a fractional number of coins.",
        ),
        "prediction": _AssetClassSpec(
            validate_prediction, PredictionStrategyCodeError, "decide_prediction",
            "def decide_prediction(as_of, positions, markets, account) -> [order_intent, ...] -- markets is "
            "[{platform: \"kalshi\"|\"polymarket\", market_id, title, outcomes: [{name, price}]}, ...]; each "
            'order_intent is {"action": "open"|"close", "platform", "market_id", "outcome", "side": '
            '"buy"|"sell", "qty"}.',
        ),
    }


@dataclass
class ExtractionResult:
    #: The final code to use, or None if nothing usable could be produced.
    code: Optional[str]
    #: True when the input needed no LLM rewrite at all (already valid).
    was_already_valid: bool
    #: Plain-language note shown to the user: what happened, or why it failed.
    summary: str


_EXTRACTION_SYSTEM_PROMPT = """You convert an arbitrary uploaded file into a working Python trading \
strategy for the "{asset_class}" market. The required contract is:

{contract}

The input may already be valid code close to this shape, a strategy written for a different market, a \
strategy described in prose, or something unrelated to trading at all. Your code will run in an \
isolated subprocess with NO imports beyond math/statistics/json/datetime, no network, no file access, \
and a hard timeout -- do not use anything else.

Respond with ONLY a JSON object, no prose outside it: {{"convertible": true|false, "code": \
"<python source defining {entry_function}, or null if not convertible>", "summary": "<one or two \
plain-language sentences: what you did, or why this can't become a {asset_class} strategy>"}}."""


def extract_strategy_code(raw_content: str, asset_class: str, *, user_id: Optional[int] = None) -> ExtractionResult:
    """Best-effort: on any failure (no LLM configured, malformed response,
    the LLM's rewrite still doesn't validate), degrades to reporting the
    original validation error rather than raising -- callers already handle
    "code rejected by static analysis" as a normal outcome."""
    spec = _specs().get(asset_class)
    if spec is None:
        return ExtractionResult(code=None, was_already_valid=False, summary=f"unknown asset class {asset_class!r}")

    raw_content = (raw_content or "").strip()
    if not raw_content:
        return ExtractionResult(code=None, was_already_valid=False, summary="the uploaded file was empty")

    try:
        spec.validate_code(raw_content)
        return ExtractionResult(code=raw_content, was_already_valid=True, summary="Uploaded code was already valid -- no changes needed.")
    except spec.code_error:
        pass  # fall through to the LLM rewrite attempt below

    from dashboard.backend.infrastructure.llm.backtest_harness import HAS_ANTHROPIC, extract_response_text
    from dashboard.backend.infrastructure.llm.providers import make_llm_client

    if not HAS_ANTHROPIC:
        return ExtractionResult(code=None, was_already_valid=False, summary="No LLM available to convert this file -- it must already define the required function.")
    client = make_llm_client(user_id=user_id)
    if client is None:
        return ExtractionResult(code=None, was_already_valid=False, summary="No LLM API key configured -- it must already define the required function.")

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            system=_EXTRACTION_SYSTEM_PROMPT.format(
                asset_class=asset_class, contract=spec.contract_description, entry_function=spec.entry_function,
            ),
            messages=[{"role": "user", "content": f"Convert this uploaded file:\n\n```\n{raw_content[:8000]}\n```"}],
        )
        text = extract_response_text(response).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        parsed = json.loads(text.strip())
    except Exception as exc:  # noqa: BLE001 -- a bad LLM turn degrades to "couldn't convert," never crashes the upload
        return ExtractionResult(code=None, was_already_valid=False, summary=f"Conversion attempt failed: {exc}")

    candidate_code = parsed.get("code")
    llm_summary = str(parsed.get("summary") or "").strip()
    if not parsed.get("convertible") or not candidate_code:
        return ExtractionResult(code=None, was_already_valid=False, summary=llm_summary or "This file doesn't look like a convertible trading strategy.")

    try:
        spec.validate_code(candidate_code)
    except spec.code_error as exc:
        return ExtractionResult(
            code=None, was_already_valid=False,
            summary=f"The converted code still didn't pass safety validation ({exc}) -- upload rejected rather than run.",
        )

    return ExtractionResult(code=candidate_code, was_already_valid=False, summary=llm_summary or "Converted into a working strategy.")


@dataclass
class AgentPromptExtractionResult:
    #: The extracted trading instruction, or None if nothing usable could be
    #: produced.
    prompt: Optional[str]
    summary: str


_AGENT_PROMPT_EXTRACTION_SYSTEM_PROMPT = """You convert an arbitrary uploaded file into a clear \
trading instruction for a {asset_class}-trading AI agent. The agent reads this instruction and \
decides its own trades from it at each step -- it is plain-language guidance, not code. The input \
may already be a clear instruction, a strategy written as code, a strategy described loosely, or \
something unrelated to trading at all.

Respond with ONLY a JSON object, no prose outside it: {{"convertible": true|false, "prompt": \
"<a clear, self-contained trading instruction in plain English, 1-4 sentences, or null if not \
convertible>", "summary": "<one sentence: what you extracted, or why this can't become an agent \
instruction>"}}."""


def extract_agent_prompt(
    raw_content: str, asset_class: str, *, user_id: Optional[int] = None,
) -> AgentPromptExtractionResult:
    """Same idea as ``extract_strategy_code``, but for My Agents: the target
    shape is a plain-language trading instruction (a pipeline step's
    ``prompt``), not executable code -- so there is no AST allowlist to
    re-validate against here. The extracted prompt is still just free text
    handed to an LLM later (the agent's own decision loop), never executed
    directly, so this module's usual "never trust the LLM's output blindly"
    rule doesn't carry the same code-safety stakes -- it can go wrong by
    being unhelpful, not by being unsafe."""
    raw_content = (raw_content or "").strip()
    if not raw_content:
        return AgentPromptExtractionResult(prompt=None, summary="the uploaded file was empty")

    from dashboard.backend.infrastructure.llm.backtest_harness import HAS_ANTHROPIC, extract_response_text
    from dashboard.backend.infrastructure.llm.providers import make_llm_client

    if not HAS_ANTHROPIC:
        return AgentPromptExtractionResult(prompt=None, summary="No LLM available to read this file.")
    client = make_llm_client(user_id=user_id)
    if client is None:
        return AgentPromptExtractionResult(prompt=None, summary="No LLM API key configured.")

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=_AGENT_PROMPT_EXTRACTION_SYSTEM_PROMPT.format(asset_class=asset_class),
            messages=[{"role": "user", "content": f"Convert this uploaded file:\n\n```\n{raw_content[:8000]}\n```"}],
        )
        text = extract_response_text(response).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        parsed = json.loads(text.strip())
    except Exception as exc:  # noqa: BLE001 -- a bad LLM turn degrades to "couldn't convert," never crashes the upload
        return AgentPromptExtractionResult(prompt=None, summary=f"Conversion attempt failed: {exc}")

    prompt = str(parsed.get("prompt") or "").strip()
    summary = str(parsed.get("summary") or "").strip()
    if not parsed.get("convertible") or not prompt:
        return AgentPromptExtractionResult(prompt=None, summary=summary or "This file doesn't look like a usable trading instruction.")
    return AgentPromptExtractionResult(prompt=prompt, summary=summary or "Converted into a trading instruction.")
