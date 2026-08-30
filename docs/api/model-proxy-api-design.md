# Metered Model Proxy API — Design Doc

Status: Draft / proposal (no implementation yet)
Owner: NewWorldTrading
Last updated: 2026-06-24

## 1. Goal

Let third parties (partners, hackathon teams, demo users) call frontier LLMs
through **our** CommonStack quota, without ever seeing our CommonStack key, and
with a **hard per-key spending cap** so a shared key can never blow the $200
budget.

In one line: *we resell/share our CommonStack quota as an OpenAI-compatible
endpoint, metered in USD per issued key.*

## 2. Non-goals (for the first cut)

- Not a billing/payments system (no Stripe, no invoicing). Budgets are granted
  manually by an admin.
- Not a full multi-tenant SaaS with self-serve signup (that's a later phase).
- Not streaming (SSE) in phase 1 — added in phase 2.
- Not embeddings / images / audio in phase 1 — chat completions only.

## 3. How it works (request flow)

```
caller (OpenAI SDK, base_url = https://OUR_HOST/api/v1/proxy/v1)
   │  Authorization: Bearer ptk_...            (a key WE issued)
   ▼
proxy router  ──1── auth: resolve ptk_ → grant (or 401)
              ──2── allowlist: model ∈ exposed set (or 403)
              ──3── budget: grant.usd_used < grant.usd_budget (or 402)
              ──4── forward to CommonStack with OUR COMMONSTACK_API_KEY
                       POST https://api.commonstack.ai/v1/chat/completions
              ──5── read response.usage → cost via token_cost.py
              ──6── atomically: usd_used += cost, write proxy_usage ledger row
              ──7── return CommonStack response body to caller
```

The caller uses the stock OpenAI SDK; only `base_url` and the API key change.

## 4. Why this is low-risk to build

We already have the hard parts:

- API-key issuance + hashing + lookup: `domain/agents/repository.py`
  (`_hash_api_key`, `_new_api_key`, `resolve_api_key`) and `protocol_auth.py`.
- SQLite storage patterns and migrations: `database.py`.
- Per-model USD cost mapping: `infrastructure/llm/token_cost.py`.
- The CommonStack endpoint is OpenAI-compatible, so the proxy body is a
  near pass-through.

## 5. Auth model

Introduce a **dedicated proxy key** type, separate from trading-agent keys, so
"can spend my LLM budget" is not conflated with "is a registered trading agent".

- Key format: `ptk_<token_urlsafe(24)>` (proxy token key). Store only the
  SHA-256 hash + a short prefix for display (mirrors `external_agents`).
- Sent as `Authorization: Bearer ptk_...` (OpenAI convention) — also accept
  `X-API-Key` for parity with the rest of the app.

Decision: **new table** over reusing `external_agents` keys. Cleaner separation,
independent revocation, and budgets don't leak across features.

## 6. Data model (SQLite)

```sql
CREATE TABLE proxy_keys (
    key_id        TEXT PRIMARY KEY,          -- uuid4
    label         TEXT NOT NULL,             -- "commonstack-demo-team-a"
    api_key_hash  TEXT NOT NULL UNIQUE,
    api_key_prefix TEXT NOT NULL,            -- "ptk_abc…" for display
    usd_budget    REAL NOT NULL DEFAULT 0,   -- hard cap
    usd_used      REAL NOT NULL DEFAULT 0,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active',  -- active | revoked
    owner_note    TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at  TIMESTAMP
);

CREATE TABLE proxy_usage (              -- per-request ledger (transparency/audit)
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id        TEXT NOT NULL,
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    status_code   INTEGER,
    FOREIGN KEY (key_id) REFERENCES proxy_keys(key_id)
);
CREATE INDEX idx_proxy_usage_key ON proxy_usage(key_id, timestamp);
```

## 7. Endpoints

All under `/api/v1/proxy`. The `/v1/...` suffix makes the OpenAI base_url clean:
`base_url = https://OUR_HOST/api/v1/proxy/v1`.

### Public (caller-facing, auth = proxy key)
- `POST /api/v1/proxy/v1/chat/completions` — the metered passthrough.
- `GET  /api/v1/proxy/v1/models` — only the models we expose (the 6).
- `GET  /api/v1/proxy/usage` — caller's `usd_budget`, `usd_used`, remaining,
  and recent ledger rows.

### Admin (auth = existing admin/owner context)
- `POST   /api/v1/proxy/admin/keys` — mint a key `{label, usd_budget}` →
  returns the **plaintext key once**.
- `GET    /api/v1/proxy/admin/keys` — list keys (prefix + usage, never the key).
- `PATCH  /api/v1/proxy/admin/keys/{key_id}` — adjust budget / revoke.
- `DELETE /api/v1/proxy/admin/keys/{key_id}` — revoke.

## 8. Guardrails (MANDATORY before any key is shared)

1. **Hard USD cap per key** — reject with `402` once `usd_used >= usd_budget`.
   Also reject if the *estimated* cost of the incoming request would exceed the
   remaining budget (estimate input tokens from the request body, cap by
   `max_tokens`).
2. **Model allowlist** — only the 6 sanctioned models (config-driven). Anything
   else → `403`. Prevents callers invoking models we didn't price/intend.
3. **Per-request `max_tokens` ceiling** — clamp `max_tokens` to a server max so a
   single call can't run away.
4. **Rate limit per key** — e.g. N req/min and M concurrent, to bound burst spend
   and protect the upstream.
5. **Atomic budget accounting** — decrement inside a single SQLite transaction
   (`UPDATE ... SET usd_used = usd_used + ? WHERE key_id = ? AND status='active'`)
   to avoid double-spend under concurrency. For higher concurrency, a short
   app-level lock around read-check-forward-write.
6. **Secret hygiene** — the CommonStack key is read from env server-side only;
   never logged, never returned, never in error messages.
7. **Global kill switch** — an env flag / admin toggle to disable the proxy
   instantly if spend spikes.

## 9. Cost accounting

- Prefer the **real** `usage` block CommonStack returns
  (`prompt_tokens` / `completion_tokens`).
- Map to USD via `token_cost.py`, extended with the exposed slugs' real rates
  (from the CommonStack model library). Reasoning models bill thinking tokens at
  output rate — already covered since CommonStack reports them as output tokens.
- Apply an optional **margin/overage buffer** (e.g. charge 1.05×) so rounding and
  any uncounted overhead never push us over the real CommonStack bill.

## 10. Config

```
COMMONSTACK_API_KEY     # server-side secret (the real quota)
COMMONSTACK_BASE_URL    # default https://api.commonstack.ai/v1
PROXY_ENABLED           # global kill switch (default false)
PROXY_MODEL_ALLOWLIST   # comma-separated slugs, or read from leaderboard config
PROXY_MAX_TOKENS        # per-request output ceiling
PROXY_RATE_LIMIT_RPM    # per-key requests/minute
```

## 11. Phasing

- **Phase 1 (MVP)**: tables + `chat/completions` passthrough + allowlist +
  USD budget + ledger + `usage` endpoint + admin mint/revoke + kill switch.
  Non-streaming. Enough to hand a partner a working, capped key.
- **Phase 2**: SSE streaming passthrough; dashboard UI for keys + usage charts;
  finer rate limiting; per-model sub-budgets.
- **Phase 3**: self-serve signup + payments, if this becomes a product.

## 12. Open questions / to confirm

- **CommonStack ToS**: is proxying/sharing quota with third parties permitted?
  (Raise in tonight's meeting — this gates whether we share keys externally.)
- Meter in **USD** (recommended) vs raw tokens?
- Expose only the 6 leaderboard models, or a broader allowlist?
- Where does admin auth come from for the key-mint endpoints (reuse existing
  admin router auth)?

## 13. Rough effort

- Phase 1: ~1 router + 1 repository module + token_cost rows + tests. Small,
  because auth/storage/cost primitives already exist.
