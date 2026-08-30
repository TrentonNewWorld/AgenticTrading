from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

from anthropic import APIError, AsyncAnthropic
from dotenv import load_dotenv

from dashboard.backend.infrastructure.llm.backtest_harness import (
    COMMONSTACK_MODEL_NAME,
    LLM_MODEL_NAME,
)


load_dotenv()


# CommonStack is the "model we host": one key reaches frontier models behind an
# Anthropic-compatible endpoint. When COMMONSTACK_API_KEY is set the chat client
# routes through it (and must use the gateway slug); otherwise it falls back to
# native Anthropic.
COMMONSTACK_BASE_URL = os.getenv("COMMONSTACK_BASE_URL", "https://api.commonstack.ai")


def require_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


def resolve_chat_model() -> str:
    """Model id matching the client ``get_claude_client`` builds.

    CommonStack expects ``provider/model`` slugs. Prefer ``CHAT_MODEL`` when set;
    otherwise use ``COMMONSTACK_MODEL_NAME`` (DeepSeek by default — Anthropic
    slugs on CommonStack have been observed returning a canned greeting with
    ~10 ``input_tokens`` while ignoring the request body).
    """
    if os.getenv("COMMONSTACK_API_KEY"):
        return os.getenv("CHAT_MODEL") or COMMONSTACK_MODEL_NAME
    return os.getenv("ANTHROPIC_MODEL", LLM_MODEL_NAME)


# Lazily-constructed Anthropic-compatible client.
#
# Importing this module must not require credentials or build a network client;
# the client is created on first use via ``get_claude_client`` so that import
# stays side-effect free and test/runtime configuration is resolved on demand.
_claude_client: AsyncAnthropic | None = None


def get_claude_client() -> AsyncAnthropic:
    """Return the shared chat client, constructing it on first use.

    Prefers CommonStack (the hosted gateway) when ``COMMONSTACK_API_KEY`` is set;
    otherwise uses native Anthropic via ``ANTHROPIC_API_KEY``.
    """
    global _claude_client

    if _claude_client is None:
        commonstack_key = os.getenv("COMMONSTACK_API_KEY")
        if commonstack_key:
            _claude_client = AsyncAnthropic(
                api_key=commonstack_key,
                base_url=COMMONSTACK_BASE_URL,
            )
        else:
            _claude_client = AsyncAnthropic(api_key=require_env("ANTHROPIC_API_KEY"))

    return _claude_client


# CommonStack Anthropic-provider stub (2026-07): ignores body, returns this
# greeting with ~10 input_tokens. Refuse and fall back to a working provider.
_STUB_ASSISTANT_GREETING = "Hi! How can I help you today?"
_COMMONSTACK_CHAT_FALLBACK_MODELS = (
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash",
)


def _is_stub_assistant_reply(
    text: str,
    *,
    input_tokens: int | None,
    prompt_chars: int,
) -> bool:
    if (text or "").strip() == _STUB_ASSISTANT_GREETING:
        return True
    # Body ignored by gateway: long prompt but almost no input tokens billed.
    if input_tokens is not None and prompt_chars > 80 and input_tokens < 20:
        return True
    return False


def _chat_model_candidates(preferred: str | None) -> list[str]:
    """Ordered models to try for chat/strategy synthesis."""
    primary = (preferred or "").strip() or resolve_chat_model()
    candidates = [primary]
    if os.getenv("COMMONSTACK_API_KEY"):
        for model in _COMMONSTACK_CHAT_FALLBACK_MODELS:
            if model not in candidates:
                candidates.append(model)
    return candidates


def _stub_reply_error(model: str, *, action: str) -> RuntimeError:
    """Error raised when every candidate model produced a stub reply.

    Only mentions CommonStack/CHAT_MODEL when a CommonStack key is actually in
    play; the native-Anthropic path has neither, so that advice would confuse.
    """
    if os.getenv("COMMONSTACK_API_KEY"):
        return RuntimeError(
            f"Hosted model {model!r} ignored the {action} request (returned a "
            f"canned greeting). Set CHAT_MODEL to a working CommonStack slug "
            f"(e.g. deepseek/deepseek-v4-pro) or pick an agent whose model is "
            f"not on the broken Anthropic route."
        )
    return RuntimeError(
        f"Model {model!r} returned a canned greeting instead of a real "
        f"{action} reply. Check ANTHROPIC_MODEL or the selected agent's model."
    )


# Temporary MVP memory.
#
# Key:
#   (platform_user_id, agent_id)
#
# Value:
#   Claude-compatible conversation messages
#
# This will eventually be replaced with persistent database storage.
conversation_history: dict[
    tuple[str, str],
    list[dict[str, Any]],
] = defaultdict(list)


SYSTEM_PROMPT = """
You are the conversational assistant for NewWorldTrading.

NewWorldTrading helps users experiment with LLM-based trading agents,
including backtesting, paper trading, strategy configuration, performance
evaluation, and decision analysis.

This Discord integration is currently an early chat prototype.

Do not claim that you:
- executed a trade,
- changed a saved strategy,
- accessed a portfolio,
- ran a backtest,
- retrieved live market data,

unless the application provides an actual tool result confirming that action.

Provide educational and research-oriented assistance. Clearly distinguish
general information from personalized financial advice.
""".strip()


def extract_text(response: Any) -> str:
    parts: list[str] = []

    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# NewWorldSupport: the Discord /support command's answerer.
#
# The knowledge base below is the ONLY source the support model may answer
# from -- the system prompt pins it there. This channel is where people ask
# real questions about software that can place real-money orders, so an
# answer improvised from a model's general training (plausible, confident,
# wrong) is strictly worse than "I don't know, here's where to ask." Keep
# this text in sync with the product; it is the support bot's entire world.
# ---------------------------------------------------------------------------

#: Update this to the real Whop listing URL before launch.
SUPPORT_WHOP_URL = "https://whop.com/YOUR-STORE-HERE"

SUPPORT_KNOWLEDGE_BASE = f"""
# What NewWorldTrading is
An open-source platform for building, testing, and running trading strategies —
backtests, simulated (paper) trading, and optional real-money (live) trading
through connected broker accounts. It runs as a local web dashboard.

# Getting the bot
The bot is FREE. Get it here: {SUPPORT_WHOP_URL}
After download, it runs locally: install Python dependencies
(`pip install -r requirements.txt`), then start it with
`uvicorn dashboard.backend.app:app` from the project root and open
http://localhost:8000 in a browser. Configuration (broker API keys etc.)
goes in `dashboard/.env`.

# Dashboard pages
- **Home**: market overview, dashboard switcher (Stocks / Options / Futures /
  Forex / Crypto / Prediction — each has its own independent paper wallet,
  but Stocks/Options/Crypto share one Alpaca account), news and signals.
- **Overview**: two leaderboards. The **Competition Leaderboard** ranks AI
  models on one fixed backtest window. The **Live Trading Leaderboard** shows
  REAL results — only strategies that actually placed orders appear, values
  come from the broker's own numbers, and the chart is the account's own
  equity history.
- **Manual**: the "Manual 10" feature — scans the first minutes after market
  open for the biggest $1–$99 gainers, buys the top picks in paper, and can
  promote winners to real money (only when explicitly enabled server-side).
  Activation is per-trading-day: it must be activated each day it should run.
- **My Agents / Playground**: create LLM-driven trading agents, run backtests,
  inspect decision logs. Each agent gets an access key for the SDK/API.
- **Testing**: upload your own strategy code. It is scanned for safety, then
  backtested over the most recent completed year with a $1,000 starting
  wallet. Strategies that pass can be added to the Strategy catalog.
- **Strategy** (catalog): every registered strategy with description, equity
  curve, and metrics. Each has "Activate Paper" and "Activate Live" buttons.

# Making your own strategy
1. Go to **Testing** in the dashboard.
2. Paste or upload your strategy code.
3. It is automatically scanned for safety and backtested (most recent
   completed year, $1,000 wallet, $10-per-trade lot sizing).
4. Review the results; if you like them, add it to the Strategy catalog.
5. From the catalog you can then activate it for Paper or Live.
You can also chat with an agent (`/ask` in Discord, or the Playground) to
turn a plain-English idea into a strategy prompt with `/strategy`.

# Paper vs. Live — how execution actually works
- **Paper** = simulated money. **Live** = real money, real orders.
- Activating a strategy in the catalog makes it run once per trading day,
  every trading day, until deactivated. Activation alone never places a real
  order: live execution ALSO requires the account-level "Live Trading"
  switch (account menu) to be on. Both must be on for real orders.
- Live orders pass a risk gate: a per-order USD cap (default $25), no
  shorting ever, sells capped to shares actually held, and buys sized
  against the strategy's own Allocated capital (set on the Strategy page),
  never the whole account.
- Each strategy's "Allocated" dollar amount and optional "$ per stock"
  override are set on its catalog card.

# Broker connections
Set up on the **Connections** page: Alpaca (paper + live, stocks/options/
crypto), OANDA practice (forex), Tradovate demo (futures), Kalshi and
Polymarket (prediction markets). Keys are stored encrypted. Alpaca paper and
live use separate key pairs — they can never be silently crossed.

# Discord commands
- `/support <question>` — this bot; answers questions about the platform.
- `/ask <prompt>` — chat with your selected trading agent (hosted model).
- `/agent` — pick which of your built-in agents Discord uses.
- `/strategy` — turn an idea or chat into a backtestable strategy prompt.
You can also DM the bot or @mention it for free-form chat.

# Common issues
- "My strategy isn't trading": check (1) it is Activated (Paper or Live) on
  the Strategy page, (2) for live: the Live Trading switch is on, (3) the
  market is open — catalog strategies run once per trading day, (4) broker
  keys are connected and valid.
- "Login screen loops / can't sign in": local installs can enable
  auto-login; check LOCAL_AUTO_LOGIN_ENABLED in dashboard/.env.
- "Backtest fails with market data errors": Alpaca keys in dashboard/.env
  may be missing or expired — regenerate them in the Alpaca dashboard and
  update the file.
- Announcements about new strategies, versions, and server updates are
  posted in the #announcements channel.

# Risk
Trading involves risk of loss. Past performance does not guarantee future
results. Simulated and backtested results do not represent actual trading
and often differ from live results. Nothing the bot says is personalized
financial advice.
""".strip()

SUPPORT_SYSTEM_PROMPT = f"""
You are NewWorldSupport, the official support assistant for the
NewWorldTrading Discord server.

Answer the user's question using ONLY the knowledge base below. Rules:
- Be accurate, direct, and friendly. Short answers over long ones.
- If the knowledge base does not cover the question, say so plainly and
  point the user to ask a moderator — NEVER guess or improvise an answer
  about how the platform works, and never invent features, URLs, commands,
  or settings that are not in the knowledge base.
- Never give personalized financial or investment advice ("should I buy X",
  "is this strategy good for me"). Explain how the platform works instead,
  and include the risk note when the question touches real money.
- When relevant, mention where to get the bot ({SUPPORT_WHOP_URL}) and which
  dashboard page or Discord command does what the user is asking about.
- Do not claim to have performed any action (running a backtest, changing a
  setting, placing a trade). You answer questions; you do not operate the
  platform.

KNOWLEDGE BASE:
{SUPPORT_KNOWLEDGE_BASE}
""".strip()


# --- /support runs on OpenRouter FREE models only ---------------------------
#
# Unlike chat_with_agent (which routes through the hosted CommonStack gateway
# on the operator's paid credits), support is a public, unauthenticated-ish
# surface: anyone in the Discord can invoke it. Pinning it to OpenRouter's
# free tier means a busy day costs nothing and cannot be turned into a bill by
# volume alone.
#
# Cost control is two-layered, because one layer is not enough:
#
#   1. Here: any model id not ending in ``:free`` is refused (and logged)
#      rather than called, so a typo'd or "helpfully upgraded" model id fails
#      closed instead of silently billing.
#   2. At OpenRouter: set a **$0 credit limit** on the key in
#      ``OPENROUTER_SUPPORT_API_KEY`` (openrouter.ai → Keys → Edit → Credit
#      limit). Free models cost $0 so they keep working; a paid model becomes
#      impossible rather than merely discouraged. Layer 1 is a guard; layer 2
#      is the guarantee. Do not rely on layer 1 alone.
#
# A dedicated key matters: OPENROUTER_API_KEY may legitimately hold paid
# credits for leaderboard models, so it cannot carry the $0 limit.
#
# OpenRouter rate-limits free models per day. Exhausting the quota raises
# (429) and the Discord layer falls back to its keyword FAQ / human
# escalation — it must never retry onto a paid model.
_SUPPORT_FREE_SUFFIX = ":free"

#: Tried in order; first one that answers wins. A LIST rather than a single id
#: because OpenRouter's free tier is genuinely flaky per-model: measured
#: 2026-08-26, four of six candidates returned a provider-side 429 within
#: seconds while this list's first entry answered in 5s. A single hardcoded
#: model means one upstream hiccup takes /support down entirely.
#:
#: The roster also churns — the previous default
#: (meta-llama/llama-3.3-70b-instruct:free) stopped being free and started
#: 404ing with "use the paid slug instead", which the free-only guard below
#: correctly refused. Re-check ids at https://openrouter.ai/models?q=free
#: (or GET /api/v1/models and filter on the ":free" suffix) when support goes
#: quiet. Override with OPENROUTER_SUPPORT_MODEL (comma-separated for a list).
DEFAULT_SUPPORT_MODELS: tuple[str, ...] = (
    "nvidia/nemotron-3-super-120b-a12b:free",
    "z-ai/glm-5.2:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
)


def support_models() -> tuple[str, ...]:
    """Free support models to try, in order. Every id must end in ``:free`` —
    a non-free id is dropped (and logged) rather than called, so the caller
    degrades to the offline FAQ instead of spending credits. Raises when that
    leaves nothing to try."""
    raw = (os.getenv("OPENROUTER_SUPPORT_MODEL") or "").strip()
    configured = (
        tuple(part.strip() for part in raw.split(",") if part.strip())
        if raw
        else DEFAULT_SUPPORT_MODELS
    )
    allowed = []
    for model in configured:
        if model.endswith(_SUPPORT_FREE_SUFFIX):
            allowed.append(model)
        else:
            print(
                f"support: refusing model {model!r} — only OpenRouter free "
                f"models (ending {_SUPPORT_FREE_SUFFIX!r}) are allowed."
            )
    if not allowed:
        raise RuntimeError(
            "support: no free model configured — every candidate was refused "
            f"for not ending in {_SUPPORT_FREE_SUFFIX!r}."
        )
    return tuple(allowed)


def get_support_client() -> AsyncAnthropic:
    """OpenRouter client for /support, on the free tier. Separate from
    ``get_claude_client`` on purpose — different provider, different key,
    different budget."""
    key = (
        os.getenv("OPENROUTER_SUPPORT_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or ""
    ).strip()
    if not key:
        raise RuntimeError(
            "support: set OPENROUTER_SUPPORT_API_KEY (a dedicated key with a "
            "$0 credit limit) or OPENROUTER_API_KEY."
        )
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api")
    return AsyncAnthropic(api_key=key, base_url=base)


async def support_answer(question: str) -> str:
    """One-shot NewWorldSupport answer, grounded in the support knowledge
    base, served by an OpenRouter **free** model. No conversation memory: each
    /support question stands alone, which keeps answers deterministic-ish and
    prevents one user's thread from steering another's. Raises on provider
    failure (including a 429 once the daily free quota is spent) — the Discord
    layer owns the fallback copy."""
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("Question cannot be empty.")

    models = support_models()
    client = get_support_client()
    last_error: Exception | None = None
    for model in models:
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=700,
                system=SUPPORT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": cleaned}],
                # Free models often don't support extended thinking, and a
                # support reply doesn't need it — it also keeps the answer
                # inside the free tier's smaller token ceilings.
                extra_body={"reasoning": {"enabled": False, "exclude": True}},
            )
        except APIError as exc:
            # Per-model 429 / 404 / 403 are routine on the free tier: the id
            # stopped being free, or that provider is saturated right now.
            # Try the next free candidate — never fall back to a paid model.
            last_error = exc
            print(f"support: model={model!r} unavailable ({type(exc).__name__}); trying next free model")
            continue
        reply = extract_text(response)
        if reply:
            return reply
        last_error = RuntimeError(f"support: model={model!r} returned an empty reply")
        print(last_error)
    if last_error is not None:
        raise last_error
    raise RuntimeError("support: no free model produced a reply")


async def chat_with_agent(
    *,
    user_id: str,
    agent_id: str,
    message: str,
    model: str | None = None,
) -> str:
    """
    Send a message to an NewWorldTrading agent.

    This function is the main integration boundary. The Discord bot should
    not call Anthropic directly.

    Tries ``model`` (or ``resolve_chat_model()``) first, then CommonStack
    fallbacks if the primary provider returns a known stub greeting or a
    request error.

    Future implementation:
    - authenticate the platform user,
    - verify agent ownership,
    - retrieve durable memory,
    - load the selected agent configuration,
    - expose approved trading tools,
    - save messages and tool results.
    """
    cleaned_message = message.strip()

    if not cleaned_message:
        raise ValueError("Message cannot be empty.")

    key = (user_id, agent_id)
    history = conversation_history[key]

    history.append(
        {
            "role": "user",
            "content": cleaned_message,
        }
    )

    # Keep only the latest six user-assistant exchanges for the MVP.
    if len(history) > 12:
        del history[:-12]

    prompt_chars = len(SYSTEM_PROMPT) + sum(
        len(str(m.get("content") or "")) for m in history
    )

    try:
        client = get_claude_client()
        candidates = _chat_model_candidates(model)
        last_stub_model: str | None = None
        answer = ""
        for index, candidate in enumerate(candidates):
            is_last = index == len(candidates) - 1
            try:
                response = await client.messages.create(
                    model=candidate,
                    max_tokens=1200,
                    system=SYSTEM_PROMPT,
                    messages=history,
                )
            except APIError:
                if is_last:
                    raise
                print(
                    f"chat: model={candidate!r} request failed; trying fallback"
                )
                continue

            reply = extract_text(response)
            usage = getattr(response, "usage", None)
            input_tokens = (
                getattr(usage, "input_tokens", None) if usage is not None else None
            )
            if reply and _is_stub_assistant_reply(
                reply, input_tokens=input_tokens, prompt_chars=prompt_chars
            ):
                last_stub_model = candidate
                print(
                    f"chat: stub reply from model={candidate!r} "
                    f"input_tokens={input_tokens}; trying fallback"
                )
                continue

            answer = reply
            break
        else:
            raise _stub_reply_error(last_stub_model, action="chat")
    except Exception:
        # Avoid retaining a user message that never received an answer.
        if history and history[-1]["role"] == "user":
            history.pop()

        raise

    if not answer:
        answer = "Claude returned an empty response."

    history.append(
        {
            "role": "assistant",
            "content": answer,
        }
    )

    if len(history) > 12:
        del history[:-12]

    return answer


def reset_agent_conversation(
    *,
    user_id: str,
    agent_id: str,
) -> None:
    key = (user_id, agent_id)
    conversation_history.pop(key, None)


# System prompt for compiling a conversation/idea into a single, self-contained
# free-form strategy prompt. The output is fed to the backtest agent each hour;
# the backtest engine appends the market snapshot + JSON output contract, so this
# must NOT specify any output format.
STRATEGY_SYNTH_SYSTEM = """You are a trading-strategy compiler for NewWorldTrading.

Read the conversation and/or idea, then output a SINGLE, self-contained trading
strategy prompt that an LLM trading agent will follow each market hour to trade
DJIA stocks in a backtest.

Output rules:
- Output ONLY the strategy prompt text. No preamble, no markdown headers, no JSON.
- Be concrete about entry rules, exit rules, position sizing, and risk, grounded
  in the signals the agent will have: price, SMA20, SMA50, MACD, RSI, recent
  momentum, current holdings, and cash.
- Do NOT describe any output/JSON format; the system adds that automatically.
- Do NOT invent data sources the agent cannot see (no live news/Twitter/APIs).
- Keep it under ~250 words and directly actionable.
""".strip()


async def synthesize_strategy_prompt(
    *,
    user_id: str,
    agent_id: str,
    extra: str | None = None,
    model: str | None = None,
) -> str:
    """Compile a user's conversation (+ optional extra text) into one strategy prompt.

    Uses the hosted chat model. Pulls the user's existing conversation history
    (from prior ``chat_with_agent`` turns) and an optional ``extra`` instruction,
    and returns a single free-form strategy prompt suitable for
    ``POST /backtest/run`` (``strategy_prompt``) — no JSON, no formatting.

    ``model`` should be the selected agent's model when available (same as
    ``/ask``); otherwise ``resolve_chat_model()`` is used, with CommonStack
    fallbacks if the primary provider returns a known stub greeting or a
    request error.
    """
    key = (user_id, agent_id)
    history = list(conversation_history[key])

    final_instruction = (
        "Compile everything above into the final strategy prompt now. "
        "Output only the strategy prompt text."
    )
    if extra and extra.strip():
        final_instruction = (
            f"Strategy idea / requirements:\n{extra.strip()}\n\n" + final_instruction
        )

    if not history and not (extra and extra.strip()):
        raise ValueError(
            "Nothing to compile: chat about your strategy first, or provide a description."
        )

    messages = history + [{"role": "user", "content": final_instruction}]
    prompt_chars = len(STRATEGY_SYNTH_SYSTEM) + sum(
        len(str(m.get("content") or "")) for m in messages
    )

    client = get_claude_client()
    candidates = _chat_model_candidates(model)
    last_stub_model: str | None = None
    for index, candidate in enumerate(candidates):
        is_last = index == len(candidates) - 1
        try:
            response = await client.messages.create(
                model=candidate,
                max_tokens=900,
                system=STRATEGY_SYNTH_SYSTEM,
                messages=messages,
            )
        except APIError:
            if is_last:
                raise
            print(
                f"strategy synth: model={candidate!r} request failed; trying fallback"
            )
            continue

        strategy = extract_text(response).strip()
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        if not strategy:
            continue
        if _is_stub_assistant_reply(
            strategy, input_tokens=input_tokens, prompt_chars=prompt_chars
        ):
            last_stub_model = candidate
            print(
                f"strategy synth: stub reply from model={candidate!r} "
                f"input_tokens={input_tokens}; trying fallback"
            )
            continue
        return strategy

    if last_stub_model:
        raise _stub_reply_error(last_stub_model, action="strategy")
    raise RuntimeError("The model returned an empty strategy prompt.")
