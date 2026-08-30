# Durable agent & strategy storage (`CONTENT_DATABASE_URL` Postgres backends)

**Date:** 2026-07-15
**Status:** **implemented** — shipped in #134, deployed, and verified live in prod on 2026-07-17 (all four stores report `backend: postgres` in the Render log; prod DDL matches this design's 15/12/8 columns). This document is a point-in-time design record, not a status page: it is accurate as *design*, but read the code as the source of truth on behaviour. Two things the build changed or added that are **not** described below — both filed as issues:
> - The twins have **no lazy-migration path**, and CI structurally cannot catch it (an empty Postgres container only ever exercises the `CREATE TABLE` path). Adding a column to `_init_schema` alone will not reach an existing deployment — see **#135** and the comment at each `_init_schema`.
> - A `require_postgres_url()` scheme guard (`dashboard/backend/db_url.py`) was added to every twin: `psycopg` parses a non-URL as a keyword DSN and quotes the whole value — password included — into its error, which the fail-loud factories put straight in the deploy log.
>
> Remaining follow-ups: **#136–#141**. Notably run history (`agent_runs`) is still ephemeral per Decision 1 below (**#140**).

**Precedent:** `docs/superpowers/plans/2026-07-08-user-account-persistence-fix.md` (the `USERS_DATABASE_URL` users fix — this design extends that exact pattern to agents and strategies)

## Problem

Prod runs on Render's free tier with no persistent disk, so the SQLite database at
`DATABASE_PATH` resets to the committed seed `backtest.db` on every deploy (and merging
to `main` auto-deploys). User accounts were rescued from this in 2026-07 via an optional
Postgres backend (`USERS_DATABASE_URL`, Neon in prod), but everything users create
*besides* accounts still evaporates:

- **Agents** (`external_agents`, created by `AgentStore._init_schema()`,
  `dashboard/backend/domain/agents/repository.py:87-155`): agents vanish from
  "My Agents", and — worse — **issued API keys stop validating**, because
  `resolve_api_key()` (`repository.py:340-358`) is the sole auth path for both
  `/api/v1` and `/api/v2`. Every SDK integration and Discord-bot agent breaks on
  every deploy.
- **Agent versions** (`agent_versions`,
  `dashboard/backend/domain/agents/version_repository.py:83-111`): immutable
  reproducibility snapshots, lost with their agents.
- **Strategies** (`strategies`,
  `dashboard/backend/domain/strategies/repository.py:63-80`): 8-char share codes die.

A structural wrinkle: prod already has a split-brain ownership model. The `users` half
of the user→agent relationship lives in durable Neon Postgres while the `agents` half
lives in wipe-on-deploy SQLite, joined by `external_agents.owner_user_id` — which is
*declared* as `REFERENCES users(id)` but never enforced (no `PRAGMA foreign_keys`
anywhere in the backend). Moving agents into the same Postgres database as users is the
only option that makes that relationship joinable again.

## Decisions (settled with Felix, 2026-07-15)

1. **Scope:** `external_agents` + `agent_versions` + `strategies` move to optional
   Postgres. Run history (`agent_runs`, `equity_timeseries`, `trades`,
   `protocol_runs`, `protocol_steps`, `idempotency_keys`, `run_manifest`) stays in
   ephemeral SQLite — explicitly a later phase (write-heavy per-step engine writes;
   Neon free tier is ~0.5 GB).
2. **Config:** one new env var **`CONTENT_DATABASE_URL`** consumed by the three new store
   factories. The users factory's backend selection is **unchanged** — it keeps reading
   `USERS_DATABASE_URL` alone, and gains only the startup log line described under
   "Failure semantics". Each Postgres-backed store is thus selected by a var named for
   what it stores; a deployment that wants everything durable sets both vars, pointing
   at the same database.

   **Naming — `CONTENT_DATABASE_URL`, not `DATABASE_URL`.** The obvious name is the
   worst available one: `DATABASE_URL` is the Heroku-style de-facto standard, which
   makes it what a managed-Postgres add-on injects automatically, what anyone attaching
   a database reaches for by default, and what a developer plausibly already exports in
   the same shell for an unrelated project. Binding the *wrong database* is the failure
   this design must not have, and one asymmetry settles it: the conftest strip (Testing
   tier 1) protects the *test process* from an ambient value, but nothing protects a
   local `uvicorn dashboard.backend.app:app` run, and nothing can — reading the ambient
   var *is* the feature. A name no other tool sets cannot be collided with by accident,
   so the collision is designed out rather than merely made visible. `CONTENT_` also
   states what the var backs — agents, versions and strategies, i.e. user-created
   *content*, as against the run history that deliberately stays in SQLite (Decision 1)
   — and mirrors the `USERS_DATABASE_URL` precedent. Cost, accepted: a fully durable
   deployment sets two vars instead of one. Note this does **not** retire the
   host/dbname log line ("Failure semantics" below): the rename kills *accidental*
   collision, not a typo'd or staging-vs-prod URL, and it does nothing about the more
   dangerous branch — an *unset* var silently selecting ephemeral SQLite.
3. **Approach:** literal copy of the proven `users.py` dual-backend pattern —
   hand-written Postgres twin class per store with an identical public method
   surface, per-store factory, no base class, no SQL-translation layer, no ORM.
   (Rejected: a home-grown dual-dialect adapter — upsert/error semantics differ
   subtly between dialects and the translation layer is where silent bugs live; and
   SQLAlchemy — a heavyweight new dependency this thin-stdlib-wrapper codebase
   doesn't need at this scale.)

## Architecture

### Backend selection

Each store module gets a factory cloned from `_build_user_store()`
(`dashboard/backend/users.py:312-321`):

```python
def _build_agent_store():
    database_url = os.getenv("CONTENT_DATABASE_URL")
    if database_url:
        from dashboard.backend.domain.agents.repository_postgres import PostgresAgentStore

        print(f"agent_store backend: postgres ({describe_database_url(database_url)})")
        return PostgresAgentStore(database_url)
    print("agent_store backend: sqlite (ephemeral on Render)")
    return AgentStore()

agent_store = _build_agent_store()
```

Module-level singleton names (`agent_store` at `repository.py:551`,
`agent_version_store` at `version_repository.py:198`, `strategy_store` at
`domain/strategies/repository.py:156`) are unchanged, so no caller changes anywhere.
Verified: no **production** code outside these three modules runs raw SQL against
these tables — all 16+ agent endpoints and the strategy routes go through the
singletons. Two *test* files are known exceptions
(`tests/test_v2_http_runs.py:49-52`, `tests/test_v2_auth.py:57-61`: they reach into
`agent_store._get_connection()` and run raw `UPDATE external_agents ... ?` SQL to set
scopes). They always run under SQLite (conftest strips the env vars), so they stay
valid unchanged — but the new `@pg_only` tests must not copy that fixture pattern;
use public store methods or dialect-appropriate SQL.

`_build_user_store()` keeps its backend selection exactly as shipped —
`os.getenv("USERS_DATABASE_URL")`, no fallback (Decision 2). It changes only to emit
the same startup log line as the three new factories, so that all four stores announce
which backend they bound (see "Failure semantics"). That log line is the whole of the
users-store diff.

### New modules

| Module | Class | Twin of |
|---|---|---|
| `domain/agents/repository_postgres.py` | `PostgresAgentStore` | `AgentStore` |
| `domain/agents/version_repository_postgres.py` | `PostgresAgentVersionStore` | `AgentVersionStore` |
| `domain/strategies/repository_postgres.py` | `PostgresStrategyStore` | `StrategyStore` |
| `db_url.py` | — (`describe_database_url()`) | — (new shared helper) |

`db_url.py` sits at the backend root, not under `domain/`, because all four factories
(the three above plus users) import it; `domain/` → backend-root imports are already
the established pattern (`from dashboard.backend.database import DB_PATH` in both
agent modules today), and it introduces no `domain/` → `api/` edge, so
`test_architecture_boundaries.py` is unaffected.

Each twin defines its own `_get_connection()` inline —
`psycopg.connect(self.database_url, row_factory=dict_row)` — exactly like
`users_postgres.py:30-31`. (A shared `pg.py` connection module was considered and
rejected: it would deviate from the precedent's shape, and the `@pg_only` test
fixtures mirror `test_users_postgres.py:56-65`, which call `store._get_connection()`
as an instance method. Consolidation across the four twins is possible later.)

Shared pure helpers are imported from each twin's SQLite module where they exist as
module-level functions — `_utcnow_iso`, `_hash_api_key`, `_new_api_key` from
`domain/agents/repository.py:29-38` — same convention as `users_postgres.py:18`
importing from `users.py`. Note the limits of this: `_utcnow_iso` is independently
defined per SQLite module (import each twin's own), and the strategies module has
**no** separable code-generation helper — the share-code retry logic is fully inlined
in `StrategyStore.create()`, so `PostgresStrategyStore.create()` reimplements that
whole method (see the dialect section for the required restructuring). Driver:
`psycopg[binary]==3.3.4`, already in `requirements.txt:49`. Connections are per-call
(no client pooling); prod **must** use Neon's pooled (`-pooler`) connection string —
with per-call connects across four stores (these three plus users), the direct
non-pooled Neon endpoint's connection cap is a real risk under concurrent agent
polling.

### Method surfaces to port (the contract, enumerated)

| Store | Public methods |
|---|---|
| `AgentStore` (`repository.py:74-548`) | `create_agent`, `register_or_get_agent`, `list_agents`, `list_builtin_agents`, `get_agent`, `get_agent_by_session`, `resolve_api_key`, `claim_browser_agents_to_user`, `claim_agent`, `reclaim_agent`, `rotate_api_key`, `update_agent`, `delete_agent`, `owns_agent` |
| `AgentVersionStore` (`version_repository.py:70-198`) | `create_version`, `get_version`, `list_versions` |
| `StrategyStore` (`domain/strategies/repository.py:50-156`) | `create`, `get`, `set_last_run` |

None use `executemany`, json1 functions, `LIKE`, or `rowid`/`lastrowid`. `update_agent`
(`repository.py:455`) builds dynamic SQL from kwargs using the `_UNSET` sentinel
(`repository.py:26`) to distinguish omitted from explicit-`None` — dialect-neutral
apart from placeholder style, but port it carefully.

### Failure semantics — fail loud, and fail visible

If `CONTENT_DATABASE_URL` is set but Postgres is unreachable, the twin's `__init__` (which
connects to run `_init_schema()`) raises and **the app refuses to start**. To be
precise about the mechanism: the singletons are bare module-level assignments reached
transitively when `app.py` imports the router chain, so this is a **raw import-time
exception** (not a FastAPI startup-hook failure), propagating from whichever store
module the import graph reaches first. Accepted as-is — it exactly matches the
`PostgresUserStore` precedent's behavior. No silent fallback to SQLite — that would
recreate the account-loss failure mode this pattern was built to kill (see CLAUDE.md,
"Fail-closed is not fail-visible").

"Fail loud" is a claim, so it gets a test: each twin has a unit test asserting that an
unreachable URL raises `psycopg.OperationalError` out of `__init__` rather than
falling back. It needs no live Postgres (a closed port refuses instantly), so it runs
everywhere, and it is what stops a later contributor from "helpfully" wrapping a
factory in a `try/except` — which would silently restore the exact failure mode this
pattern exists to kill.

The dangerous case is the *other* branch: `CONTENT_DATABASE_URL` simply **unset** silently
selects ephemeral SQLite, and nothing in the app today logs which backend won (true
even for the shipped users store). Each factory (including the users one) therefore
emits exactly one startup log line naming the chosen backend.

**The log line must identify *which* Postgres, not just "postgres".** A bare
`agent_store backend: postgres` is byte-identical whether the store bound to the
intended Neon database or to something else entirely — the same
indistinguishable-success shape CLAUDE.md's "Fail-closed is not fail-visible" section
was written about. So the line carries the resolved host and database name:

```
agent_store backend: postgres (ep-xyz-pooler.eu-central-1.aws.neon.tech/atl)
agent_store backend: sqlite (ephemeral on Render)
```

The target string is produced by one shared stdlib-only helper,
`dashboard/backend/db_url.py::describe_database_url()` (`urllib.parse.urlsplit` →
`hostname[:port]/dbname`). It is defined **once**, not cloned per store, for a
deliberate reason that overrides this design's otherwise-strict copy-the-precedent
rule: it is credential-scrubbing code, and four hand-copied scrubbers is four chances
for one of them to leak the password into a log. It is a pure string helper, not a
connection layer, so it does not reintroduce the shared `pg.py` that was rejected
above. A unit test asserts the password never survives the transformation.

**Emit with `print()`, not `logger.info()` — this is load-bearing, not style.** Under
the configuration prod actually runs, `logger.info()` from a `dashboard.backend.*`
module **emits nothing**. Verified, not assumed: nothing under `dashboard/backend/`
calls `basicConfig`/`dictConfig`/`setLevel`; no launch path passes `--log-level`
(`render.yaml:13`, `Dockerfile:26`, `app.py`'s `__main__`); and uvicorn's
`LOGGING_CONFIG` defines no `root` key, so these loggers inherit root's default
`WARNING`. Reproduced directly: `getEffectiveLevel()` → `WARNING`,
`isEnabledFor(INFO)` → `False`, `logger.info(...)` → silence.

A `caplog`-based test cannot catch this, because `caplog.at_level(logging.INFO, ...)`
force-sets the level for the duration of the test: the suite goes green while prod
stays silent. That is precisely the green-tests/invisible-prod shape this whole design
is meant to eliminate — the fail-visible mitigation would itself have failed
invisibly. `print()` is also the codebase's overwhelming convention for operational
diagnostics (~25 modules, including `app.py`'s startup `DATABASE DEBUG` block and
`domain/leaderboard/service.py`), so it is idiomatic here rather than a new pattern,
it lands in Render's logs next to the existing startup output, and — unlike a level —
there is nothing about it a later cleanup can silently suppress. Tests assert on
`capsys`, not `caplog`.

## Postgres schema

Dialect conventions follow `users_postgres.py`: `TEXT` ISO-8601 timestamps (the SQLite
stores already write `_utcnow_iso()` strings, so ordering/comparison parity is exact),
JSON kept as `TEXT` (not JSONB), `DOUBLE PRECISION` for `cash_allocation`, idempotent
`CREATE TABLE IF NOT EXISTS` in `_init_schema()`.

**Lazy `ALTER TABLE ADD COLUMN` migrations — none as designed, since amended for the
agent twin.** As specified, this section argued none were needed: `users_postgres.py`
needs its `ADD COLUMN IF NOT EXISTS discord_user_id` because its `users` table already
exists in prod Neon from before Discord linking shipped, whereas all three tables here
are created *fresh* on first Postgres startup (there is no data to migrate — see
Migration below), so `CREATE TABLE` alone already declares every column.

That reasoning holds for the *first* deploy and fails for every one after it. Once the
table exists, `CREATE TABLE IF NOT EXISTS` silently no-ops, so a column added later to
the `CREATE` block alone never reaches an existing deployment, and every query naming it
raises `UndefinedColumn` — 500ing that surface while `/health` stays green. Issue #135
closed that gap for `agents/repository_postgres.py`, which now carries five
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements mirroring the SQLite store's
accreted migrations. `agent_versions` and `strategies` still carry none: they shipped
complete and have accreted no columns since, so for those two the original rule stands —
columns added in *future* work use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, the
Postgres analogue of the SQLite stores' `PRAGMA table_info` probing.

All unique constraints and indexes carry over:
`api_key_hash` UNIQUE, `session_id` UNIQUE, indexes on `owner_user_id`,
`owner_browser_session`, `agent_type`, `agent_versions(agent_id, created_at)`.

**Deliberate deviation — no FK constraint on `owner_user_id`.** It stays a plain
`INTEGER`. Rationale: SQLite never enforced it (no `PRAGMA foreign_keys` anywhere in
the backend), the app already owns this integrity (`owns_agent`,
`claim_browser_agents_to_user`), and declaring it fails in both configurations: in
the same-database config it makes agent-store init order-dependent on the users table
already existing; in the split config (`USERS_DATABASE_URL` and `CONTENT_DATABASE_URL`
pointing at different databases) it is categorically impossible — Postgres does not
support cross-database foreign keys at all. In the recommended prod config (same Neon
DB) real joins become possible; enforcement stays app-level, as today. (For scope
clarity: `agent_versions` declares no FK to `external_agents` in SQLite either —
`version_repository.py:87-101` — so no equivalent question arises there.)

### Dialect-sensitive idioms (verified against the three modules)

- `?` placeholders → `%s` (everywhere).
- `PRAGMA table_info` column probing (AgentStore's lazy migrations,
  `repository.py:123-146`) → `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. This is the
  mapping to reach for when a *future* column is added; do **not** port the existing
  probes, whose only job is to retrofit columns onto SQLite databases that predate
  them. The Postgres tables are born with every column (see above).
- **`INSERT OR IGNORE` / `INSERT OR REPLACE` do not appear in any of the three
  in-scope modules** — every insert is a plain `INSERT`. (Both idioms exist only in
  `database.py`'s out-of-scope run tables.) Do not spend implementation time hunting
  for them.
- **The strategy share-code retry loop must be restructured, not translated.**
  `StrategyStore.create()` (`domain/strategies/repository.py:95-128`) opens one
  connection and catches `sqlite3.IntegrityError` per attempt, retrying on the same
  connection — up to 20 times, then widening the code space. That structure is
  invalid in Postgres: the first `UniqueViolation` aborts the transaction, and every
  subsequent statement on that connection raises
  `psycopg.errors.InFailedSqlTransaction` — a literal port 500s on the first real
  collision. **Required approach:** per attempt, run
  `INSERT INTO strategies (...) VALUES (...) ON CONFLICT (code) DO NOTHING` and treat
  `cursor.rowcount == 0` as the collision signal (no exception handling in the retry
  path at all). Preserve the retry count and code-space-widening behavior exactly.

## Performance trade-offs (accepted, with eyes open)

- `resolve_api_key()` runs on every agent-authenticated request and performs a read
  plus a `last_used_at` UPDATE — two Neon round-trips per request.
  `register_or_get_agent()` (`repository.py:211-252`) is worse: up to three
  sequential connect/query/close cycles per call. Accepted because the users store
  already does per-request Postgres session lookups, and the pooled URL amortizes
  connection setup. If latency ever matters, the existing TTL cache
  (`dashboard/backend/cache.py`) can front the hash→agent lookup. Future work, not
  built now.
- **Neon free-tier cold starts:** compute suspends after idle minutes and takes
  multi-second wakes. Protocol runs have a 30s per-step decision deadline; a cold
  start stacked on LLM latency eats into that budget after quiet periods. Accepted
  for now (a late decision auto-holds the step rather than failing the run); if it
  bites, a keep-alive ping is the escape hatch.

## Migration, seeding & rollout

- **No data migration:** prod agent/strategy data evaporates by definition; first
  Postgres startup auto-creates empty schema.
- **Built-in agents:** they are ordinary `external_agents` rows with
  `agent_type='builtin'` created via the normal API (the two in the seed DB are just
  data). Post-cutover they are recreated once through the API and persist thereafter.
  No code-level seeding.
- **Rollout — env var FIRST, then merge.** Merging to `main` auto-deploys prod, and
  an *unset* `CONTENT_DATABASE_URL` silently selects ephemeral SQLite (only set-but-unreachable
  fails loud) — so merging first would open an invisible window where the feature
  doesn't work. Order: (1) set `CONTENT_DATABASE_URL` in the **Render dashboard** (same Neon
  database as `USERS_DATABASE_URL` — which itself lives only in the dashboard, not in
  `render.yaml`; the yaml is known-drifted from prod and editing it alone does
  nothing) → (2) merge; CI auto-deploys on green main → (3) confirm the new
  `agent_store backend: postgres (<host>/<db>)` startup log lines — all four of them,
  and check the host/db actually matches the intended Neon database rather than just
  that the word "postgres" appears → (4) **live verification:** create an agent,
  trigger a redeploy, confirm the agent still lists and its API key still resolves.
  Optionally add `CONTENT_DATABASE_URL` to `render.yaml` with `sync: false` as documentation,
  clearly not as the mechanism.

- **Publish the merge gate where GitHub enforces it, from the moment the PR opens.**
  "Set the env var before merging" is an ordering constraint that exists only in this
  document — and this repo has no branch protection, no required checks, and a
  demonstrated norm of merging open PRs unreviewed (see CLAUDE.md, "Merge & branch
  discipline"; PR #107 is the case study, where a gate posted as a comment arrived 41
  minutes after someone had already merged). So the implementation PR **opens as a
  draft**, with `DO NOT MERGE until CONTENT_DATABASE_URL is set in the Render dashboard` as
  the literal first line of its body, and is marked ready-for-review only once the
  var is set. A gate recorded in a plan file, a session's memory, or a comment is not
  a gate.

## Testing

The users fix established three tiers. Cloning them as-is would ship this change's
entire Postgres half **unexecuted**, so a fourth (tier 0) is added first and is the
most important item in this section.

0. **CI must run a real Postgres — the `@pg_only` tier has to be a gate, not a
   courtesy.** `.github/workflows/ci.yml` today has no `services:` block, so
   `TEST_POSTGRES_URL` is never set and every `@pg_only` test skips on every PR. That
   was survivable for the users fix (one store, already shipped and since proven in
   prod); here it would mean ~450 lines of new SQL across three modules reaching prod
   having never executed once. And the blast radius is not "a bug": `_init_schema()`
   runs at **import time** and this design mandates fail-loud, so a single DDL typo
   means the app raises on boot. Merging `main` auto-deploys, and the free tier has
   no zero-downtime deploys — so a typo plausibly takes prod down rather than merely
   failing a deploy. Add a `postgres:18-alpine` service to the `backend-tests` job
   and set `TEST_POSTGRES_URL` (~8 lines of YAML). This lands *early* in the plan, not
   at the end: it also switches on the shipped-but-never-CI-run
   `test_users_postgres.py` live tier, and that wants to shake out on its own commit
   rather than tangled with new code.
1. **Whole suite stays on SQLite:** `dashboard/backend/tests/conftest.py` must strip
   `CONTENT_DATABASE_URL` at import time, alongside the existing `USERS_DATABASE_URL` strip
   (`conftest.py:44-47`). Without this, a dev with prod env vars set would run the
   test suite against the production Neon database. Note this strip is **load-bearing
   for tests that already exist**, not just a new safety net: two of them assert the
   *singleton* is a SQLite store by reaching for `.db_path`
   (`tests/domain/agents/test_repository_move.py:39-40` and
   `tests/domain/strategies/test_strategy_store.py:42-43`). Once the factories exist,
   an ambient `CONTENT_DATABASE_URL` would break those with an `AttributeError`, not just
   redirect their writes. (Tests that construct a store directly — e.g.
   `test_agent_repository_compatibility.py`'s `repos` fixture — are indifferent to the
   env var, since `__init__` never reads it.)
2. **Dispatch tests** per factory: `CONTENT_DATABASE_URL` set → Postgres twin selected;
   unset → SQLite. The users factory gets the same pair on `USERS_DATABASE_URL`, plus
   one test pinning that it **ignores** `CONTENT_DATABASE_URL` — the vars are scoped
   per store (Decision 2), and that separation is only a claim until something asserts
   it. No live Postgres needed (mirror `test_users_postgres.py`'s dispatch pair). Each
   asserts the backend log line, including the resolved host/dbname target.
3. **Fail-loud tests** per twin: an unreachable URL must raise
   `psycopg.OperationalError` out of `__init__` rather than falling back. No live
   Postgres needed (a closed port refuses instantly), so these run in every
   environment — and unlike the dispatch tests (which monkeypatch the twin class
   away) they are the only tier that executes the twins' real `__init__`.
4. **`@pg_only` behavioral tests** per store, gated on `TEST_POSTGRES_URL` (mirror
   `test_users_postgres.py`'s live tier): agent create/claim/rotate/resolve lifecycle
   including browser-session→user claim; `update_agent` partial updates (`_UNSET`
   sentinel); version immutability; `last_used_at` update on resolve; and the
   strategy share-code collision path — which must **force a real collision**
   (monkeypatch `secrets.token_hex` to return a duplicate before a fresh value), not
   mock the insert.

**Where the strategy collision tests go.** No existing test forces a collision on
either backend, so this coverage is new for SQLite too — but it belongs in the
**existing** `dashboard/backend/tests/domain/strategies/test_strategy_store.py`
(whose `test_codes_are_unique`, lines 79-82, only checks that 25 random codes don't
collide by chance, and whose `_store(tmp_path)` helper at lines 18-19 the new tests
should reuse). Do **not** create a second `test_strategy_store.py` at
`dashboard/backend/tests/` — the tests tree is a package (`__init__.py` throughout),
so a duplicate basename would not error, it would just quietly split one store's
tests across two identically-named files.

## Config & docs surface

- `.env.example`: document `CONTENT_DATABASE_URL` next to `USERS_DATABASE_URL` (`:76-83`),
  explicitly contrasting with `DATABASE_PATH` (local SQLite file for everything vs
  durable Postgres for user-created content).
- `render.yaml`: optionally add `CONTENT_DATABASE_URL` with `sync: false` as documentation —
  the real mechanism is the Render dashboard (see Rollout).
- `.github/workflows/ci.yml`: add a `postgres:18-alpine` service + `TEST_POSTGRES_URL`
  to the `backend-tests` job, so the `@pg_only` tier runs (Testing tier 0). Not
  cosmetic — this is the only thing that executes the new SQL before prod does.
- CLAUDE.md: extend the env/credentials section and the user-account-persistence
  gotcha to cover agents/strategies.

## User-facing docs made true by this change (follow-ups, not edited mid-session)

These currently promise durability the platform doesn't deliver; once this ships they
become accurate and should be revisited:

- `docs/source/lab/external_agents.rst:50-53` (persistent `session_id`), `:73`
  (resolve api_key "at any time"), `:310` (leaderboard ranking of registered agents)
- `docs/api/python-sdk-quickstart.md:161-182` ("create agent once, reuse across runs";
  `create_agent_version`)
- `packaging/newworldtrading/README.md:66` (register on dashboard to get an API key)
- `docs/architecture/discord-to-backtest.md:9` (create a built-in agent first)
- `README.md:50-61` (customizable agents, leaderboard competition)

## Out of scope

- Run-history / leaderboard durability (phase 2 candidate; revisit Neon storage
  budget and per-step write chattiness then)
- Caching `resolve_api_key()` lookups
- ORM / migration-framework adoption
- Render paid tier / persistent disk changes
