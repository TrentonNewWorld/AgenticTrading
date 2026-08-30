# Consuming FinSearch News-Sentiment Signals (Plan 1)

**Audience:** whoever maintains `dashboard/backend/integrations/news_sentiment.py`.
**Status (2026-07-16):** **shipped on both sides and live.** The FinSearch producer is live; the adapter this document specifies is built and consumed by the fail-closed loader. Read what follows as *the contract as shipped*, not as a work order — where this doc and the module disagree, the module wins and this doc is the bug.
**See also:** [`finsearch-news-items.md`](finsearch-news-items.md) — the shared `news-story v2` story vocabulary and the raw-items endpoint behind the Home panel's "Latest news" column (Phase B).

---

## What this is

Plan 1 of the three-plan programme **News Feed → Sentiment → Agent Trading Lab**: turn FinSearch's daily news feed into a **per-ticker sentiment signal** — a **thin sentiment pass** we compute over the feed we own — and inject it at `snapshot["news_sentiment"]` so news becomes an **auditable, sourced signal** a human can read and a trading agent can act on (closed loop / 链路闭环). The goal is **measurement, not alpha-seeking**: we are building the apparatus that lets us observe how agents trade on real news, not shipping a money-maker.

The sentiment computation lives behind **one frozen seam**. When the group's dedicated "Sentiment Signals of Financial News" work (Qinchuan & Chris) lands, it replaces the producer's internals and this output contract is unchanged.

---

## The consumer seam (already built in this repo)

No ATL-side contract change is needed — the `/api/v2` refactor shipped the whole consumer side as forward-compat. The adapter only has to populate it:

- **Loader:** `execution/backtest_backend.py` → `load_news_sentiment(universe, timestamp)` is **fail-closed**: it imports `dashboard.backend.integrations.news_sentiment.get_news_sentiment(universe, timestamp)` and degrades to `({}, None)` if the module is missing or raises. The `news_sentiment` slot is therefore **always present** in the context envelope (defaults to `{}`).
- **Adapter interface to implement:**
  ```python
  def get_news_sentiment(universe: list[str], timestamp) -> dict:
      # returns {"news_sentiment": dict[str, NewsSentimentEntry-shaped],
      #          "news_overview": str | None}
  ```
- **Consumer type** (`api/v2/models.py`, `NewsSentimentEntry`), keyed by symbol:
  | field | type | constraint |
  |-------|------|-----------|
  | `sentiment` | str | `bullish` \| `bearish` \| `neutral` |
  | `score` | float | −1.0 … 1.0 |
  | `headline` | str | |
  | `source` | str | |
  | `url` | str | |
  | `age_hours` | float | ≥ 0.0 |
  | `n_articles` | int | ≥ 0 |
  | `rationale` | str \| None | optional — see design note |

> **Note the two vocabularies.** `NewsSentimentEntry.score` above is the
> **internal** envelope key and is *not* a missed rename: the wire sends
> `sentiment_score`, the adapter reads that and emits `score` internally. The
> two are decoupled on purpose, so "rename the wire" never means "rename the
> consumer type." Signals v2 shipped 2026-07-15 (FinSearch #361, ATL #115) and
> the transitional v1 `score` fallback is **deleted** — v2 is a hard rename,
> not a dual-write, and FinSearch normalizes at its API boundary, so `score`
> cannot appear on the wire from either the latest read or `?as_of`.

- **Universe:** the loader is called with `list(DJIA_30)` (the canonical current Dow-30 constant, `infrastructure/llm/validator.py`, reconciled in #91/#94 — the old `AMEX` typo is gone). Pass that straight through as the `?tickers=` filter.

---

## The producer endpoint (live)

```
GET https://agenticfinsearch.org/api/signals/news/
```

| Query param | Meaning |
|-------------|---------|
| `?as_of=YYYY-MM-DD` | Return the newest artifact whose filename stem-date ≤ `as_of`. Malformed/empty → `400 bad_as_of`; a date before recorded history → `404`; a future date → the latest dated artifact. **This is how backtests avoid look-ahead** (see below). |
| `?tickers=AAPL,MSFT,…` | Restrict `signals` to this subset. Pass the DJIA-30 universe. |

Response headers: `Cache-Control: public, max-age=300`; an `ETag` of the form `"<generated_at>|<source_items>|<filter>"`. For a conditional GET **prefer `If-None-Match`** over `Last-Modified` (the latter only covers the unfiltered variant) → `304` when unchanged.

Only tickers that actually had qualifying news appear in `signals`; a quiet ticker is simply **absent** (not a zero entry). That is intentional — a mention-only or subject-less story is dropped by the producer's relevance gate, so absence means "no directional read," not "score 0."

---

## Auth (new since 2026-07-12 — REQUIRED)

`GET /api/signals/news/` is now **bearer-gated** in production. A request with no key, or a wrong key, returns **`401`** (verified live). Two things follow for the adapter:

1. **Send the header on every request:**
   ```
   Authorization: Bearer <FINGPT_API_KEY>
   ```
2. **Provision the key in ATL's Render environment** as a secret env var `FINGPT_API_KEY`, set to the **same value as the FinSearch backend key**. The adapter reads it from `os.environ`.

This shared key is a **coarse gate** (it raises the bar against drive-by API abuse), **not** per-user auth — per-user attribution is deferred to a future login/identity system on the FinSearch side. Do not treat it as a secret boundary between ATL users; do treat it as a required credential.

---

## Producer response shape (`signals-v2`)

Top level — the fields the **public, bearer-gated** response carries (consume by key, not position; JSON member order is not significant): `schema_version` (=2), `profile`, `generated_at` (ISO-8601), `source_items`, `window_hours`, `watchlist[]`, `status` (`ok` | `degraded`), `status_reason`, `news_overview`, `diagnostics{…}`, `signals`, and `staleness_hours` (server-computed hours since `generated_at`, appended last by the view).

> **Do not** expect `generator` / `model` / `prompt_version` on the wire: the producer strips them from every public response via `_PUBLIC_STRIP` in `signals_views.py`. Conversely `staleness_hours` is **injected** there (after the strip, so it serializes at the end of the object) and is the documented origin of the panel's "Updated Xh ago" header — it is not part of the raw on-disk artifact.

`signals` is an object keyed by ticker; each value:

```json
"MSFT": {
  "sentiment": "bullish",
  "sentiment_score": 0.5,
  "rationale": "Two distinct outlets report upbeat Azure guidance.",
  "headline": "Microsoft raises Azure guidance after record quarter",
  "source": "Reuters",
  "url": "https://example.com/msft-1",
  "published": 1783335600.0,
  "guid": "fix-msft-1",
  "n_articles": 2
}
```

The authoritative JSON Schema and a committed golden fixture live in the FinSearch repo at `Heartbeat/schemas/signals-v2.schema.json` and `Heartbeat/tests/fixtures/signals-fixture.json`. Both are copied **verbatim** into `dashboard/backend/tests/fixtures/` so the adapter has an **offline/CI target** that needs no network or key. Re-copy them, never hand-edit them: `test_signals_fixture_validates_against_the_vendored_producer_schema` checks the pair against each other, which is what keeps the copies honest — before it existed the vendored schema was inert and sat at v1 for a day after the producer moved to v2, with nothing red.

---

## The projection: `signals[TICKER]` → `NewsSentimentEntry`

The producer artifact and the consumer type were designed independently and **do not match one-to-one**. The adapter's real job is this mapping:

| `NewsSentimentEntry` | ← source | transform |
|----------------------|----------|-----------|
| `sentiment` | `sentiment` | passthrough — the enums are identical (`bullish`/`bearish`/`neutral`) |
| `score` | `sentiment_score` | passthrough (already −1…1) |
| `headline` | `headline` | passthrough |
| `source` | `source` | passthrough |
| `url` | `url` | passthrough |
| `n_articles` | `n_articles` | passthrough |
| `age_hours` | `published` (epoch) | **`max(0.0, (reference_ts − published) / 3600.0)`** |
| `rationale` | `rationale` | passthrough — optional; the producer's one-line directional reasoning (see design note) |
| — | `guid` | **dropped** — no slot in `NewsSentimentEntry` (see design note) |

And `news_overview` ← the top-level `news_overview` string (passthrough).

**`reference_ts` is the `timestamp` argument, not wall-clock.** In a backtest, `timestamp` is the step's *simulated present*, so `age_hours` must be measured against it — computing age against `datetime.now()` would leak the real present into a historical step and inflate every age. Derive both the request key and the age reference from the one `timestamp` arg:

- `as_of = timestamp.date()` → `YYYY-MM-DD`
- `reference_ts = epoch(timestamp)`

---

## Backtest stepping (no look-ahead)

`load_news_sentiment(list(DJIA_30), step["timestamp"])` is called **once per backtest step**. Because the engine replays real historical windows, each step must see only signals that existed by that date. `?as_of=<step date>` gives exactly that: the newest artifact dated on or before the step. A missing day (before history, or a gap) returns `404` → the adapter yields `{}` for that step, which the fail-closed loader already tolerates. Never fall back to "today's" signal for a past step.

---

## Error & degraded handling (fail closed, log loud)

The consumer slot already defaults to empty, so every failure mode collapses safely to "no news this step." That safety is exactly the hazard: **a collapsed slot and a quiet news day are the same object**, so the log level is the only thing separating "nothing happened" from "the contract moved." Map them explicitly:

| Condition | Adapter behavior |
|-----------|------------------|
| `401` | Missing/wrong `FINGPT_API_KEY`. Return `{}`; **log an error** — this is a misconfiguration, not "no news." |
| `503` | FinSearch auth misconfigured server-side. Return `{}`; log. |
| `400 bad_as_of` | Bad date derivation in the adapter. Return `{}`; log (adapter bug, not data). |
| `404` | No artifact at/before `as_of`. Return `{}` (normal for early steps). |
| body `status: degraded` | Data is **still usable** — project it as normal, but surface `status_reason` (log / overview) so the run is auditable. |
| network/timeout | Return `{}`; log. Do not retry hard inside a per-step call. |
| **one signal is off-spec** (missing/renamed key, wrong type) | Drop **that ticker only**, `WARNING` naming the missing key, project the rest. A dropped ticker is indistinguishable from a quiet ticker — which is already this dict's shape (only tickers *with* news appear), so the blast radius is the broken ticker, not the step. Until #122 this was a dict comprehension: one off-spec ticker raised out of the whole projection and blanked the sentiment slot for **every** ticker that step. |
| **every** signal is off-spec (non-empty artifact → 0 usable entries) | Wire-shape drift, not "no news": `ERROR` via `_alarm_if_all_dropped`. An **empty** artifact is not drift — nothing was dropped. A signal filtered out by the `?tickers=` universe is not drift either; the alarm compares against the *considered* subset, not the raw block. |

**Why `ERROR` and not `WARNING`.** Per-entry warnings are what a producer rename looked like on 2026-07-14 — routine noise, for hours, under a green suite and an HTTP 200. A wholesale drop is the only signal that distinguishes *absent* from *broken*, and it has to be loud enough to be found. See [`finsearch-news-items.md`](finsearch-news-items.md) §"Drift escalates `status`…" for what each caller does with it — and note that on **this** (backtest) path the alarm is **log-only**: there is no per-step status field to raise, and giving drift a real channel is open issue #123. `_project_entry` deliberately keeps **no v1 `score` fallback**: a `KeyError` there means the contract broke and must be seen, not smoothed over.

---

## Design note — `rationale` / provenance

The producer emits a one-line **`rationale`** per ticker (why the read is bullish/bearish) plus the story `guid`. `NewsSentimentEntry` now carries an **optional** `rationale: str | None = None` field (additive, 2026-07-13), so the agent can fold the sourced reasoning into the decision `reasoning` field that already persists. It is a forward-compatible, additive change (existing consumers ignore it) and it restores the "auditable, sourced signal" thesis: `headline` + `source` + `url` + `rationale` together give provenance and directional reasoning, instead of the reasoning being discarded before the agent ever sees it. The projecting adapter (see the Traceability section) is responsible for passing the producer's `rationale` through into this slot.

`guid` has **no slot** and stays dropped — `headline`/`source`/`url` already give sufficient provenance without it, and there is no plan to add one.

---

## Where the shipped adapter puts each of these

This section used to carry a reference sketch. It is **deleted, deliberately**:
the adapter is built, so a sketch of it was a second and worse copy of the truth,
and it drifted exactly as you'd predict — its projection loop had no per-ticker
isolation, which is the defect #122 had to fix in the real module while the
"reference" for that module went on demonstrating it. The projection table above
is the contract; `dashboard/backend/integrations/news_sentiment.py` is the
reference. Read the module.

| Concern | Where it lives |
|---------|----------------|
| Transport, auth header, memo/negative cache, `?as_of` / `?tickers` | `fetch_signals` (+ `_http_get`, `_headers`) |
| `signals[TICKER]` → `NewsSentimentEntry` | `_project_entry` (story triple via the shared `_story_fields`) |
| Per-ticker drop isolation | `_project_step_signals` |
| Wholesale-drift `ERROR` | `_alarm_if_all_dropped` (log-only on this path — issue #123) |
| Entry point the loader calls | `get_news_sentiment` |

Two facts the sketch carried that are worth keeping in prose:

- **`timestamp` arrives as an ISO string, not a `datetime`.** The real call site
  (`external_run_service.py`) serializes it before the loader ever sees it, so
  `_coerce_timestamp` coerces rather than assumes — and returns `None` for junk,
  which `get_news_sentiment` fails closed on.
- **ATL does not compute sentiment.** The producer already computes it over the
  feed (`Heartbeat/news_signals.py`); this adapter consumes the result. There is
  no local scoring path, and adding one would fork the frozen seam this document
  opens with.

---

## Traceability

| Section | Anchor |
|---------|--------|
| Framing, "Plan 1", "one frozen seam", measurement-not-alpha | `FinSearch-to-ATL-Integration-Plan.html` (2026-06-23, reconciled 2026-07-06, shipped-half 2026-07-07) |
| Producer contract, `signals-v2`, `as_of`, relevance gate | FinSearch `Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md`; `Heartbeat/schemas/signals-v2.schema.json` |
| Consumer seam, `NewsSentimentEntry`, fail-closed loader | ATL `api/v2/models.py`, `execution/backtest_backend.py`; `docs/superpowers/specs/2026-06-23-agent-api-foundation-design.md` |
| Auth requirement | FinSearch `Docs/superpowers/plans/2026-07-12-endpoint-auth.md` (PRs #354/#355/#356); endpoint verified live 2026-07-13 |
| `as_of`, Dow-30 reconcile | FinSearch PR #340 / #341; ATL #91 / #94 |
| Per-entry drop isolation + wholesale-drift `ERROR` on the step path | ATL PR #122; `_project_step_signals`, `_alarm_if_all_dropped`; `test_one_malformed_step_signal_does_not_blank_the_other_tickers` / `test_every_step_signal_dropped_alarms_instead_of_reading_as_a_quiet_news_day` / `test_universe_filter_alone_is_not_drift` |
| Backtest drift is log-only (no per-step status channel) | ATL open issue #123 |

**Deliberate deviation from the plan:** the plan's frozen sketch listed `rationale`/`published` on the injected entry; the shipped `NewsSentimentEntry` carries `age_hours`/`n_articles` instead of `published`, plus (as of 2026-07-13) an optional `rationale`. This doc documents the shipped contract rather than assuming the older sketch.
