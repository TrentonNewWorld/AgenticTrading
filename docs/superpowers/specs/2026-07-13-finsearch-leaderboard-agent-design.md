# Agentic FinSearch as a Leaderboard Trading Agent (Plan 3) — Design Spec

- **Date:** 2026-07-13
- **Owner:** Felix (FlyMiss)
- **Repos:** agent-trading-lab (provider + entry) **and** Agentic-FinSearch (trader personality)
- **Status:** Design — written under the AFK planning loop; open forks decided with rationale below and flagged for Felix's review in the PR
- **Programme:** Plan 3 of 3 (Plan 1 = news→signals, sibling spec `2026-07-13-finsearch-news-signals-panel-design.md`; Plan 2 = agent-API foundation, shipped)
- **Precedent:** the six-LLM leaderboard onboarding (PRs #72/#74/#75) and the freshest add-a-model example, PR #96 (OpenRouter Nemotron) — this spec deliberately follows that recipe.

## 1. Goal

Agentic FinSearch (AF) trades the leaderboard contest **exactly like the other model entries**: same `LLMAgentStrategy` loop, same prompt, same H6 integrity guard, same contest window (2026-04-15 → 2026-05-15, 161 hourly decision steps), same deploy path (`deploy_leaderboard_model.py` → seed-DB commit). Once the run lands, the frontend displays the entry with **zero frontend changes** (rendering is fully data-driven — verified: `leaderboard.js` has no name-keyed logic; unmatched labels get auto-assigned palette colors).

AF today has no trading behavior and no "professional trader personality." Both are added: the personality lives in **AF's repo** (it is AF's product surface, reusable beyond ATL); the integration shim lives in **ATL** (a provider module, like OpenRouter's).

## 2. Approaches considered

| Approach | Verdict | Why |
|---|---|---|
| **A. ATL provider module over AF's OpenAI-compatible `/v1/chat/completions`** | **Chosen** | "Just like other models" literally: a fourth sibling in `infrastructure/llm/providers/` (`commonstack`, `openrouter`, `anthropic` exist). Reuses the entire proven pipeline — prompt, parsing, counters, H6, deploy, display. Smallest new surface; PR #96 is a 1:1 template. |
| B. AF as an external agent driving `/api/v2` protocol runs | Deferred (future benchmark phase) | This was Plan 3's original sketch, but v2 runs don't feed the leaderboard contest table, "like other models" points at the provider path, and the full agentic value of it only materializes with live/paper mode (Phase B — not built). Revisit when Phase B lands; see §8. |
| C. Add AF's underlying foundation model directly via an existing provider | Rejected | Measures Gemini/GPT, not AF. No AF surface involved — defeats the goal. |

## 3. The integrity fork (the one hard problem)

AF's default `/v1` modes (`thinking`, `normal` — currently identical) attach the **full 46-tool live catalog** (yfinance, TradingView, SEC EDGAR, web scrape, Playwright) with `max_turns=30`. In a **backtest over past dates**, live tools are **structural look-ahead**: the agent can read July's prices/news while "trading" April 15th. Every other leaderboard model sees only the prompt's market snapshot; an AF entry with live tools could not honestly share their board.

**Decision: the leaderboard entry runs AF's *direct, tools-off* path with the trader persona.** AF already has the exact precedent: `Buffet-Agent` skips the agent/tool machinery entirely (`create_agent_response` branches on provider config) and applies a server-side persona (`BUFFETT_INSTRUCTION`). We generalize that mechanism (config-driven, buffet becomes its first user) and add a **`FinSearch-Trader`** model entry: AF's configured foundation model + AF's professional-trader persona + no tools. That is what "AF the trading agent" *can honestly mean under backtest rules*.

**The no-tools property is enforced structurally, not by operator discipline** — two independent guards:

1. **AF-side dispatch guard:** `direct: True` models bypass the agent/tool machinery at **every** dispatch site — but the four existing `provider == "buffet"` literals are two different mechanisms and only one generalizes:
   - **Tools-bypass gates** — `create_agent_response` (`datascraper.py:1015`) gains `model_config.get("direct")` routing to the non-agent path; `create_agent_response_stream` (`:1259`) gains the same direct-model branch (routing to the generic non-agent path, **not** the buffet branch); `create_advanced_response` (research — today has **no** check at all, so a `mode: "research"` request would silently reach the 46-tool pipeline) gains an entry guard. Persona selection in `_prepare_messages` (`:284`) generalizes via the `persona` config key.
   - **Buffet's HF-endpoint transport gates** — the `provider == "buffet"` checks inside `create_response` (`:531`) and its streaming twin select Buffet's *dedicated HuggingFace Inference Endpoint* (`_call_buffet_agent`, own API key/URL — nothing to do with tools-off). They **stay keyed on the provider literal**: generalizing them to `direct` would misroute every direct model into Buffet's fine-tuned endpoint instead of its own configured provider client (`clients["google"]`/`clients["openai"]`).
   The routing-matrix test (§4.3) asserts at the provider-client seam — FinSearch-Trader must reach the generic provider dispatch, never `_call_buffet_agent`, never the agent runner — for every mode and transport.
2. **ATL-side tripwire:** AF's tool-using paths populate the response's `sources` array; the direct path returns it empty. The finsearch shim **raises** if `sources` is non-empty — the step falls to rule-based fallback, coverage collapses, and H6 refuses to publish the run. A silent look-ahead curve cannot reach the board even if AF-side routing regresses.

Guardrail (advisor memo, carried through the programme): **measurement, not alpha-seeking** — we are benchmarking an agent's trading behavior on the shared apparatus, not shipping a money-maker.

**Display honesty:** every other leaderboard row names its real driving model ("Claude Sonnet 4.6", "Gemini 3.1 Pro Preview"…). This entry does the same — the `model` display field names the underlying brain and the constraint, e.g. `"FinSearch Trader — Gemini 3 Flash (tools off)"` — so a viewer never mistakes the entry for the live-tool research agent. Zero frontend changes still hold (it's a data field).

## 4. AF-side design — the professional trader personality (own PR in Agentic-FinSearch)

### 4.1 Persona + direct-path mechanism (generalize the buffet special case)

- `MODELS_CONFIG` (`Main/backend/datascraper/models_config.py`) gains a new entry. **Field names verified against the real schema** (every entry uses `model_name`, read at `datascraper.py:219,524,1012`; the streaming flag is `streaming`, read at `:1384`):

```python
"FinSearch-Trader": {
    "provider": "google",                     # same family as the FinGPT default;
    "model_name": "gemini-3-flash-preview",   # provider "google" already works on the
    "direct": True,                           # direct path (FinGPT uses it via
    "persona": "trader",                      # create_response today) — verify live
    "max_tokens": 1_048_576,                  # at impl time; sanctioned fallback:
    "streaming": False,                       # provider "openai" / gpt-5.1-chat-latest
    "description": "Professional trader persona — direct path, no tools (backtest-safe)",
},
```

- The **tools-bypass** gates (`create_agent_response:1015`, its streaming twin `:1259`) and **persona** selection (`_prepare_messages:284`) become config-driven `direct`/`persona` (buffet's entry gains `direct: True, persona: "buffett"`; the replaced literals are removed, not kept as dead `or` disjuncts). The **Buffet HF-endpoint** gates inside `create_response:531`/its streaming twin stay `provider == "buffet"` — they select a transport, not a tools policy (§3).
- **Dispatch guard:** `create_advanced_response` (research mode) short-circuits `direct` models to the non-agent path before any tool/agent machinery, so no mode/transport combination can attach tools to a direct model.

### 4.2 The persona text — a prompt file, not a Python constant

AF already has the convention for canonical instruction text: markdown files under `prompts/` loaded by code (`datascraper.py::_load_security_fragment()` delegates the security rules to `prompts/_security.md` citing "single source of truth"; `PromptBuilder` hot-reloads `prompts/core.md` on mtime). The trader persona follows it:

- **`Main/backend/prompts/personas/trader.md`** — the personality text (draft below; final wording in the AF PR).
- **`Main/backend/prompts/personas/buffett.md`** — `BUFFETT_INSTRUCTION`'s text migrates here (behavior-preserving; completes the pattern instead of leaving a double-standard in the same file).
- A small loader (same idiom as `_load_security_fragment`) resolves `model_config["persona"]` → `prompts/personas/<name>.md`, with the previous constants as in-code fallback if the file is missing.

Draft persona text (`trader.md`):

```
You are FinSearch Trader, a disciplined professional equities trader. You manage
positions with a risk-first process: capital preservation precedes return-seeking;
you size positions to conviction and cut losers rather than average down into a
failing thesis. You reason strictly from the evidence you are given — prices,
technical indicators, portfolio state, and any provided news — and you never invent
data you were not shown. When evidence is mixed you prefer holding over churning,
and you can say why in one or two sentences. When a request specifies an output
format or schema, you follow it exactly: no extra prose, no markdown fences, no
disclaimers.
```

Design notes: the persona is **format-agnostic** (the last sentence defers to the caller's requested schema rather than baking ATL's JSON contract into AF) — it stays reusable for any consumer. The "never invent data" clause is the persona-level echo of the no-look-ahead rule.

### 4.3 What the AF PR contains

1. Persona files (`prompts/personas/{trader,buffett}.md`) + loader + config-driven `direct`/`persona` on the tools-bypass and persona gates (Buffet's HF-endpoint gates stay literal — §4.1) + the research-mode dispatch guard.
2. `MODELS_CONFIG["FinSearch-Trader"]` entry (§4.1).
3. Tests: **routing matrix** — `FinSearch-Trader` × `{normal, thinking, research}` × `{stream, non-stream}` all take the direct path (no agent/tool machinery; assert at the provider-client / `_call_buffet_agent` seam — note there are **no** existing buffet tests to copy, so build these from scratch); persona file loads and lands in the instruction payload; buffet behavior unchanged; `/v1/models` lists the new key (add a positive membership assertion — `TestModelsList` only checks `len(data["data"]) > 0` today, there is no hardcoded model count to update).
4. Docs: `Docs/source/api_reference.rst` model table gains the entry.

**Not** in the AF PR: any ATL-specific trading schema, any new mode, any change to the agent/tool path's behavior for non-direct models.

**Branching:** AF's `CONTRIBUTING.md` documents a `fingpt_backend_dev → fingpt_backend_prod → main` staging chain with `[user]_[focus]` branch names; recent operational precedent (news-signals #338, endpoint-auth #354–#356 — all `Main/backend` changes) merged PRs to `main` directly. Default to the operational precedent (PR to `main`), name the branch `flymiss_trader-persona`, and let Felix retarget if he wants the formal chain.

## 5. ATL-side design — the `finsearch` provider

New sibling module `dashboard/backend/infrastructure/llm/providers/finsearch.py`, registered in `PROVIDERS` (`providers/__init__.py:31-35`). Contract compliance (per `providers/__init__.py:1-13`): expose `INTEGRATION_ID = "finsearch"`, `DEFAULT_MODEL = "FinSearch-Trader"`, `default_model_name()`, and `make_client(anthropic_cls)` that returns `None` (never raises) when `FINGPT_API_KEY` is unset.

- **Transport: the `openai` SDK, not hand-rolled HTTP.** `openai==1.101.0` is already pinned in `requirements.txt` (currently unused anywhere in `dashboard/`) — `OpenAI(base_url=f"{FINSEARCH_BASE_URL}/v1", api_key=FINGPT_API_KEY, timeout=150, max_retries=1)` provides auth, pooling, retries, and typed parsing for free; AF's required `mode` extension field rides in `extra_body`. The module's only real job is the **Anthropic-shape translation**: `content=[…type="text"…]` + `usage.input_tokens/output_tokens` — exactly what `extract_response_text` (`backtest_harness.py:163-190`) and `extract_token_usage` (`:192-206`) read.
- **`mode` is hardcoded to `"normal"`.** No `FINSEARCH_MODE` env var: the tools-off invariant must not be one unreviewed env line away from violation (an operator copy-pasting `FINSEARCH_MODE=thinking` from a staging env would hand a backtest the 46-tool catalog, and H6 checks coverage, not look-ahead). Likewise no `FINSEARCH_MODEL` env knob — `leaderboard.json`'s `model_id` is the single source of truth and the harness always passes `model=` explicitly.
- **Address the model by its registry key, never the raw upstream `model_name`.** `model_id` (and the provider's `DEFAULT_MODEL`) must be `"FinSearch-Trader"` — AF's `MODELS_CONFIG` **display key** — not the underlying `model_name` `"gemini-3-flash-preview"`. That name is **shared** by two AF entries, `FinGPT` (tools on) and `FinSearch-Trader` (direct, tools off), so it is ambiguous: AF resolves an exact display key precisely, but a raw `model_name` only by a reverse scan over the model table. AF's **routing** dispatch already resolves an ambiguous shared name to the `direct`/no-tools entry (AF PR #358), so a mistaken raw name still won't hand a backtest the tool catalog — but leaning on that is a look-ahead-safety trap, not the contract. The registry key is the only exact, self-documenting handle; keep it in `leaderboard.json`'s `model_id` and let the harness pass it verbatim.
- **`sources` tripwire (§3 guard 2):** if the response carries a non-empty `sources` array, raise — never return tool-derived content as a decision.
- Env: `FINGPT_API_KEY` (shared with Plan 1 — one secret), `FINSEARCH_BASE_URL` (default `https://agenticfinsearch.org`). Documented in `.env.example`.
- **Never auto-selected**: like OpenRouter, `resolve_integration()` only picks it when `integration: "finsearch"` is explicit.
- **Known coupling:** `make_llm_client()` gates on the `anthropic` SDK importing (`providers/__init__.py:67-68`) before any provider dispatch — so the finsearch integration still transitively requires the anthropic package to import, even though the shim never uses it. Documented in the module docstring so a missing-anthropic failure isn't misdiagnosed as a key problem.
- **Test obligations:** the new-module tests (mirror PR #96's additions in `tests/llm/test_providers.py`) **plus** updating the pre-existing golden-set assertion `test_known_integrations_are_parallel_siblings` (`test_providers.py:19-23` — a hardcoded `{"commonstack", "openrouter", "anthropic"}` set that fails the moment finsearch registers; same staleness family as the route-contract freezes that blocked PRs #88–#91).

Known contract asymmetries, accepted and documented in the module docstring:
- AF ignores `max_tokens`/`temperature` (not read by `/v1`); `LLM_MAX_OUTPUT_TOKENS` is a no-op for this provider.
- AF's `usage` is a char-count estimate (`len//4`), not a tokenizer — cost figures are approximate.
- AF requires `mode`; the shim supplies it (OpenAI clients don't send one).

Config + pricing:
- `dashboard/config/leaderboard.json` entry (the **only** authoritative copy of this block — the plan references it):

```json
{
  "id": "agentic_finsearch",
  "name": "Agentic FinSearch",
  "label": "Model",
  "model": "FinSearch Trader — Gemini 3 Flash (tools off)",
  "provider": "the upstream lab",
  "strategy": "llm_agent",
  "integration": "finsearch",
  "model_id": "FinSearch-Trader",
  "mode": "safe_trading",
  "symbols": [],
  "auto_compute": false
}
```

(If the AF PR lands on the openai fallback, the `model` display string changes to match — display honesty per §3.)
- `token_cost.py` `_PRICING_TABLE` row for `"FinSearch-Trader"` at the underlying foundation model's list price (exact numbers verified at impl time), so `est_cost_usd` approximates AF's real upstream spend rather than falling to the `(1.0, 5.0)` default.
- `deploy_leaderboard_model.py`'s docstring/help text lists the required secret per integration — add `FINGPT_API_KEY` for `finsearch` (today it names only COMMONSTACK/OPENROUTER/ANTHROPIC keys).

## 6. The run — ops constraints (all verified against current code)

| Constraint | Value | Consequence |
|---|---|---|
| AF per-identity daily budget | `AGENT_DAILY_RUN_BUDGET = 100/day` per IP (`api/agent_budget.py:44-46`); **every** `/v1` call consumes a slot | **The cliff is deterministic, not probabilistic:** once exhausted, every remaining call gets an OpenAI-style **503** (`BudgetExceeded` → `_busy_response()` in `openai_views.py`, "never a 500" — **not** a 429; the 429 path is the *separate* 600/h rate limiter, last row) and every remaining step falls back rule-based — a *contiguous* failure block that guarantees H6 rejection on a single-day full run. And the worst case is not 161 calls: the no-text retry ladder (below) can bill up to **5 calls per step** (805). Ops gate: raise the droplet env to **1000** for the run window; **confirming the raise is a hard precondition of the full-window run** (the 1-day smoke, ~7 calls, cannot catch it). Revert after the run (mandatory step, not a suggestion). |
| ATL retry behavior | The only retry is the no-text ladder (`portfolio_manager.py:311-354`): up to 4 retries + 1 "rescue" per step, triggered solely by empty-text responses. Network/HTTP errors (429/5xx/timeout) skip it entirely → immediate rule-based fallback for that step (`:468-471`) | The rescue attempt force-sets `OPENROUTER_REASONING_EFFORT` — a no-op for finsearch, so all 5 attempts are byte-identical. The persona's format-compliance clause is the real mitigation for empty/miswrapped text; if the smoke run shows any no-text retries, treat it as an AF-side bug, don't tune ATL. (Pre-existing note: that rescue mutates process-global env — a shared-state hazard when v2 live calls interleave; not introduced or worsened by this plan, just inherited.) |
| AF gunicorn timeout | 120s/request | Direct path is a single foundation-model call (no tools) — typically seconds. Ample headroom. Caveat on the wall-clock estimate: each `/v1` call also rebuilds a session context server-side (`openai_views.py:73-81,222-228`), so budget ~an hour for the full window, not minutes. |
| H6 guard | `llm_decisions / decision_steps ≥ 0.95` (`service.py:39`) → ≤8 failed steps of 161 | The persona's format-compliance clause + ATL's tolerant JSON extraction (`_parse_llm_response` finds the first `{...}`) keep steps parseable. Never pass `--allow-fallback`. |
| Rate limit | 600/h per IP on AF (the **real** 429 source — a distinct `django-ratelimit` limiter, unlike the daily-budget 503 above) | 161–805 calls spread over the run's wall-clock — no issue: even the 805 worst case takes ~5× the ~1h base wall-clock (per-call cost × call count), so the realized rate stays ~161/h, well under 600/h. |

**Run sequence** (identical to the six-LLM onboarding, per the recipe):
1. Smoke: `python3 dashboard/scripts/deploy_leaderboard_model.py --entry agentic_finsearch --start 2026-04-15 --end 2026-04-16` — catches auth/model-key/JSON issues for cents.
2. **Gate:** confirm the droplet `AGENT_DAILY_RUN_BUDGET` raise is live (a curl loop or the droplet env file), then run the full window: same command without dates. Expect H6 to pass or fail loudly (exit 2).
3. Seed refresh: commit the regenerated `dashboard/storage/data/backtest.db` from an isolated worktree (`VACUUM INTO` snapshot; WAL mode — never commit with a live handle open), alongside the config/provider diffs. The leaderboard write is **additive** (a new `agent_runs` row) — the seed runs `dashboard/config/defaults.json` references are untouched, but per the CLAUDE.md gotcha, verify they still resolve before committing.
4. Revert the budget raise (mandatory).
5. Merge → CI deploys prod automatically (PR #95 deploy hook) → `GET /api/v1/leaderboard?refresh=true` → verify the entry renders.

## 7. Display

No frontend work. Verified: the leaderboard table, chart, legend, and detail panel key on generic fields (`team_name`, `model`, `is_model`, `entry_type`, `equity_curve`); the only name-keyed logic is a cosmetic style preset for 5 baseline labels; new labels auto-assign palette colors (`leaderboard.js:25-31,40-64`). The entry appears exactly like the other six models once the run row exists — with the underlying model disclosed in the `model` display string (§3).

## 8. Future work (explicit, out of scope)

- **AF full-agentic benchmark** via `/api/v2` protocol runs in **live paper-trading** mode, where tools are legitimate — gated on Execution Phase B (`execution/paper_backend.py` is a stub today). This is where approach B comes back, and where Plan 1's signals (already in the v2 context envelope) meet Plan 3's agent.
- Feeding `news_sentiment` into the **internal** leaderboard prompt (`portfolio_manager.py` snapshot) — would change what *all* entries see; a deliberate new-contest-season decision, not a rider on this work.
- A `rationale`-aware trading log surface (the persona can say *why*; ATL persists `reasoning` per decision already).

## 9. Open questions flagged for Felix (non-blocking)

1. **Underlying model for `FinSearch-Trader`**: provider `google` is confirmed workable on the direct path in principle (FinGPT itself runs provider `google` through `create_response`) — the impl-time task is a live verification; the sanctioned fallback is `openai`/`gpt-5.1-chat-latest`. Either is honest as "AF's configured brain" (and the leaderboard `model` string discloses whichever ships).
2. **Cost display**: `est_cost_usd` will be an *approximation of AF's upstream spend* (char-estimate tokens × underlying list price). Alternative is displaying 0 (misleading) or omitting (inconsistent). Spec picks the approximation.
3. **AF branching**: PR to `main` per operational precedent, or the CONTRIBUTING.md staging chain (§4.3)?
