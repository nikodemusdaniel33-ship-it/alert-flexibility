# Engineering log

**Purpose.** This file exists so any AI session (a fresh Claude Code
session, or any other assistant) picking up this repo can get full
context on how the system works and what's happened to it, without
re-deriving it from git log or asking the human to re-explain. It is not
a substitute for `README.md` (which documents *what the system does and
how to run it*, kept accurate as of the current code) — this file
documents *why it's built this way* and *what changed, when, and what
was learned doing it*.

**Standing rule: update this file on every change.** Any AI session that
adds, changes, or fixes something in this repo — code, schema, Railway
config, anything — MUST append an entry to the Changelog section below
before finishing, and update the Architecture/Operations sections if the
change affects them. This is not optional and not just for large
changes. A future session's entire ability to pick up context depends on
this file actually being current as of the last push — an out-of-date
ENGINEERING.md is worse than none, because it will be trusted. If you are
an AI reading this to start a task: read this whole file first, then
`README.md` for exact current run instructions, then git log for
anything this file's Changelog doesn't yet cover (it should cover
everything, but verify).

---

## System overview

Two independent subsystems share one Postgres database and one repo:

1. **Live alerting pipeline** (`app/`, tables `users`/`projects`/`gaps`) —
   monitors a tracked list of crypto projects for CMC-vs-CoinGecko data
   completeness gaps (missing/different socials, missing contract
   addresses per chain) and alerts on Telegram. Has a Telegram-login-gated
   web dashboard (`app/main.py`) for reviewing, resolving, and muting
   gaps, plus a full side-by-side CMC-vs-CoinGecko detail page per
   project. Runs continuously via `app/worker.py` on a periodic cron
   (Railway `worker` service). This is the original, production system —
   see `README.md`'s "How tracking works" for the full mechanics.

2. **Market-data reference pipeline** (`app/market_data/`, `scripts/`,
   tables `cmc_top600`/`cmc_binance_listed`/`cmc_aster_listed`/
   `cmc_bybit_listed`/`cmc_okx_listed`/`cmc_universe`/`cmc_cg_mapping`/
   `cmc_field_details`/`cg_field_details`/`coin_field_contrast`/
   `gap_details`/`detail_pull_failures`) — a separate, standalone system
   built this session for a much broader question than subsystem 1
   answers: not "does this one tracked project have a gap," but "across
   every coin CMC tracks as available on Binance/Aster/Bybit/OKX (plus
   the top-600 by market cap), where does CMC's data fall short of
   CoinGecko's, systematically." Not yet wired into the live alerting
   worker — it's its own pipeline, run on its own Railway cron service
   (`market-data-cron`), feeding a set of Postgres VIEWs meant to back a
   future dashboard. See "Market-data reference pipeline" below for the
   full architecture.

Both subsystems independently discover/match CMC↔CoinGecko coins using
the same key-free public API approach (`app/criteria/market_universe.py`)
but subsystem 2 does NOT read or write subsystem 1's tables, and vice
versa — the only shared thing is the database and this matching code.

---

## Market-data reference pipeline

### Why it exists

The live alerting pipeline (subsystem 1) only tracks whatever's in
`config/projects.yaml` (manually curated) plus, optionally,
`MarketUniverseProvider`'s live auto-tracking (top-N by market cap ∪
Binance-Spot-listed, both from CMC's own data — see `README.md`). That
answers "alert me when a *tracked* project has a gap." It does not answer
"how good is CMC's data *in general*, across everything CMC lists as
tradeable on a given exchange" — answering that needs a full,
independently-refreshable snapshot of every coin on each exchange, a
reviewed CMC↔CoinGecko mapping (not just best-effort live matching), and
a persisted field-by-field comparison — none of which the live pipeline
keeps around after a worker run. That's what this pipeline builds.

### Table inventory

| Table | Populated by | Semantics |
|---|---|---|
| `cmc_top600` | `scripts/pull_top600.py` | Replace (full snapshot every run) |
| `cmc_binance_listed` | `scripts/pull_binance_listed.py` | Replace |
| `cmc_aster_listed` | `scripts/pull_aster_listed.py` | Replace |
| `cmc_bybit_listed` | `scripts/pull_bybit_listed.py` | Replace |
| `cmc_okx_listed` | `scripts/pull_okx_listed.py` | Replace |
| `cmc_universe` | `scripts/build_cmc_universe.py` | Replace (5-way union of the above) |
| `cmc_cg_mapping` | `scripts/import_cmc_cg_mapping.py` | Upsert by `cmc_id`, from `data/cmc_cg_mapping.csv` |
| `cmc_field_details` | `scripts/full_detail_pull.py` | Replace per `cmc_id` |
| `cg_field_details` | `scripts/full_detail_pull.py` | Replace per `cg_id` |
| `detail_pull_failures` | `scripts/full_detail_pull.py` | Upsert (only currently-outstanding failures) |
| `coin_field_contrast` | `scripts/build_field_contrast.py` | Replace per `cmc_id` |
| `gap_details` | `scripts/build_field_contrast.py` | Replace per `cmc_id` |

Every "Replace" table holds a single current snapshot, never history — a
deliberate, uniform convention across the whole pipeline (was append-only
for `cmc_top600`/`cmc_binance_listed` until 2026-09-21, converted for
consistency once `cmc_aster_listed` was added and needed the same
choice made from scratch).

Five exchange-listing tables (`cmc_top600`, `cmc_binance_listed`,
`cmc_aster_listed`, `cmc_bybit_listed`, `cmc_okx_listed`) all share one
shape: `cmc_id, name, symbol, slug, cmc_rank, is_spot, is_perpetual,
is_futures, fetched_at` (top600 has no `is_*` columns — CMC's listing API
doesn't break down by market-pair category the way the exchange
market-pairs API does). Adding a 6th exchange means: add its CMC exchange
slug to `app/criteria/market_universe.py`, add a `fetch_cmc_<x>_listed_ids()`
thin wrapper around the already-generalized `_fetch_cmc_exchange_listed_ids`,
copy `scripts/pull_aster_listed.py` verbatim with the name swapped, add
the model class, and extend `build_cmc_universe.py`'s union +
`cmc_universe`'s `on_<x>` column + precedence chain. See the
2026-09-22 Bybit/OKX changelog entry below for exactly this, done twice.

### Read-side VIEWs (not tables)

Two scripts create plain (non-materialized) Postgres VIEWs — no rebuild
step of their own; every read re-executes the underlying `SELECT`
live against current table state:

- `scripts/create_coins_with_gaps_view.py` → `coins_with_gaps`
- `scripts/create_dashboard_views.py` → `unmapped_coins`,
  `missing_detail_pulls`, `needs_reconfirm_mappings`,
  `gap_summary_by_field_type`, `universe_overview`, `common_missing_chains`

Read from Python via `app/market_data/views.py`, which keeps its `Table`
objects on a separate `MetaData()` from `app.db.Base` specifically so
`ensure_schema()`'s `create_all()` never tries to `CREATE TABLE`
something that's actually a view. Proven live (not just asserted) that a
plain view has zero staleness: inserted a new `coin_field_contrast` row
directly and confirmed it appeared in `coins_with_gaps` with no rebuild
script run in between.

See `README.md`'s "Market-data reference pipeline" section for the full
per-script breakdown, exact columns, and run order — kept current there
rather than duplicated here.

### Key design decisions (with rationale)

- **Cascade via direct Python function call, never a DB trigger.** Every
  pull script calls `build_cmc_universe.run()` at the end of its own
  `run()`; `full_detail_pull.py` calls `build_field_contrast.run(cmc_id=...)`
  per coin right after each periodic commit. A PL/pgSQL trigger doing the
  same thing was considered and rejected every time this came up (Binance
  → Aster → Bybit/OKX, and again for field-contrast) — it would mean the
  union/contrast logic exists in two places (Python and SQL) that could
  silently drift apart. One implementation, called directly, has zero
  sync risk. The tradeoff is `build_cmc_universe` running redundantly
  multiple times in one daily chain run (harmless — idempotent, no API
  calls, sub-second).
- **`ensure_schema()` raises on a column mismatch, never auto-drops.**
  Used to `drop_all()` the *entire* database on any single stale table;
  changed once real user data (`users`/`projects`/`gaps`) existed. Now:
  creates missing tables, raises `RuntimeError` naming exactly which
  table is missing which column if an existing table doesn't match its
  model. **This means adding a column to an existing table (e.g.
  `cmc_universe.on_bybit`) needs an explicit `ALTER TABLE` run BEFORE
  anything that calls `ensure_schema()` — including every pull script —
  or they'll all fail with that RuntimeError.** A brand-new table (e.g.
  `cmc_bybit_listed` itself) needs no such step; `create_all()` makes it
  automatically.
- **Replace semantics everywhere in this pipeline.** No batch history on
  any table here — every run is a full, atomic delete+reinsert snapshot.
  Simpler to reason about, and nothing in this pipeline currently needs
  "what did this look like yesterday."
- **Precedence order for `cmc_universe`'s denormalized name/symbol/
  rank/slug: `cmc_top600` > `cmc_binance_listed` > `cmc_aster_listed` >
  `cmc_bybit_listed` > `cmc_okx_listed`.** Arbitrary but consistent —
  top600 first because it's the most likely to have a clean canonical
  name; the rest in the order each source was added.
- **CMC's public, undocumented site API, not the documented Pro API**,
  for exchange listings — the Pro-API equivalent
  (`/v1/exchange/market-pairs/latest`) is gated to Hobbyist tier+, not
  usable on a Basic/free key. `CMC_PUBLIC_MARKET_PAIRS_URL` mirrors what
  `coinmarketcap.com/exchanges/<slug>/` calls client-side. Being
  undocumented, it could change or break without notice — always
  curl-verify a new exchange slug live before trusting it (see the
  Bybit/OKX entry below for exactly that check).

---

## Production operations

### Railway services

Three services in the `alert-flexibility` Railway project (one Postgres,
shared):

- **web** — the live dashboard + API (`app/main.py`).
- **worker** — the live alerting cron (`app/worker.py`), runs
  periodically.
- **market-data-cron** — this pipeline's daily chain (`cronSchedule: 0 2
  * * *`, `restartPolicyType: NEVER`). Current permanent `startCommand`:
  `python -m scripts.pull_top600 && python -m scripts.pull_binance_listed && python -m scripts.pull_aster_listed && python -m scripts.pull_bybit_listed && python -m scripts.pull_okx_listed && python -m scripts.build_cmc_universe`
  (the trailing explicit `build_cmc_universe` call is redundant with each
  pull script's own cascade, kept anyway as a belt-and-suspenders final
  step — cheap, and guarantees a fresh `fetched_at` timestamp covering
  everything).

### The "startCommand-hijack" pattern for one-off production runs

Railway cron services only start their container at the scheduled tick,
not on deploy — so getting a one-off script to run in production (a
migration, a backfill, a view rebuild, a manual `full_detail_pull`) needs
this procedure, used repeatedly and reliably this session:

1. Set `cronSchedule` to `null` and `startCommand` to the one-off command
   (chain multiple with `&&`; a failure partway stops the chain and
   shows in the deploy logs).
2. Reconnect the service's source (same repo/branch it's already on) —
   this triggers a fresh deploy, which now actually runs the container
   (no `cronSchedule` means it's not cron-gated).
3. Poll deployment status until `SUCCESS`/`FAILED`/`CRASHED`.
4. Fetch deploy logs to confirm what actually happened (row counts,
   errors) — don't trust "SUCCESS" alone; the script's own log lines are
   the real verification.
5. Restore `cronSchedule` and `startCommand` to their permanent daily
   values.

Do the schema/data change (e.g. an `ALTER TABLE`) *before* anything in
the same chain that calls `ensure_schema()` — see the `ensure_schema()`
note above.

Tooling: this can be done via the Railway CLI (`railway api -f
<graphql-file> --variables @<json-file>` for step 1, `railway service
source connect --service <name> --repo <owner/repo> --branch main` for
step 2, `railway deployment list --json` / `railway logs --deployment
<id>` for steps 3-4) or via the Railway MCP server's tools
(`update-service`, `connect-service-source`, `list-deployments`,
`get-logs`) — functionally equivalent; use whichever is authenticated in
the current session (the CLI's login can expire between sessions/
containers; the MCP tools authenticate independently).

---

## Changelog

Newest first. Each entry: what changed, why, and anything a future
session needs to know that isn't obvious from the code/README alone.

### 2026-09-22 — Add Bybit and OKX as 4th/5th exchange sources

Added `cmc_bybit_listed`/`cmc_okx_listed` tables and
`scripts/pull_bybit_listed.py`/`scripts/pull_okx_listed.py`, mirroring
`pull_aster_listed.py` exactly. Both exchanges verified live via curl
against `CMC_PUBLIC_MARKET_PAIRS_URL` before writing any code — CMC uses
plain `bybit`/`okx` slugs, no `aster-pro`-style surprise (spot/perpetual/
futures counts as of verification: bybit 536/733/46, okx 1366/467/192).
`cmc_universe` extended to a 5-way union with `on_bybit`/`on_okx` flags,
precedence chain extended to `top600 > binance > aster > bybit > okx`.
Also extended the dashboard views (`coins_with_gaps`, `unmapped_coins`,
`universe_overview`) to carry the two new source flags, since those
views exist specifically to show per-source breakdowns —
`universe_overview.all_three_count` renamed to `all_five_count` to stay
accurate. `README.md`'s "Deploying on Railway" section was also fixed to
actually list `market-data-cron` as a real service (previously only
documented `web`/`worker`, a pre-existing gap noticed and fixed here
since this change directly touches that service).

Production rollout order mattered: `cmc_universe` already existed
without `on_bybit`/`on_okx` columns, and `ensure_schema()` raises on a
column mismatch rather than auto-migrating — so the `ALTER TABLE` had to
run as its own step *before* `pull_bybit_listed`/`pull_okx_listed` (both
call `ensure_schema()` first thing) or every subsequent step in the
chain would fail. Applied via the startCommand-hijack pattern: one
combined one-off command (`ALTER TABLE` via a raw `engine.connect()`
call → `pull_bybit_listed` → `pull_okx_listed` →
`create_coins_with_gaps_view` → `create_dashboard_views` → a verification
query printing `universe_overview`), then daily chain + cron restored.

Created this file (`ENGINEERING.md`) in the same push, per explicit
request: a standing log any AI session should read first and update on
every future change.

### 2026-09-22 — 6 more dashboard/monitoring views + mapped_count fix

Added `unmapped_coins`, `missing_detail_pulls`, `needs_reconfirm_mappings`,
`gap_summary_by_field_type`, `universe_overview`, `common_missing_chains`
(`scripts/create_dashboard_views.py`), recommended and built as a batch
ahead of the future dashboard. Caught and fixed a real bug during
production verification (not caught by the original local test, which
had a fixture that happened to avoid the distinguishing case):
`universe_overview.mapped_count` used an unscoped subquery counting
every valid row in the historical `cmc_cg_mapping` table, independent of
`cmc_universe` — could exceed `total_coins` (seen live: 1750 vs 1115).
Fixed to a `LEFT JOIN` scoped to `cmc_universe`, matching `unmapped_coins`'
existing pattern. Strengthened the local test with mapping rows
deliberately outside the test universe to actually exercise this case
going forward.

### 2026-09-21/22 — coins_with_gaps view; full_detail_pull → build_field_contrast cascade

Added `coins_with_gaps` (`scripts/create_coins_with_gaps_view.py`) — one
row per coin with at least one `coin_field_contrast.gap=true` row, gap
counts per `field_type`. Proved live (inserted a row directly, no rebuild
script run) that a plain Postgres VIEW needs no rebuild step — every read
re-executes the SELECT against current data.

Made `full_detail_pull` automatically cascade into
`build_field_contrast.run(cmc_id=...)` per coin, so `coin_field_contrast`/
`gap_details` are never more than one batch behind without a separate
manual step. Deliberately deferred until just after each periodic DB
commit (not called inline per coin) — `build_field_contrast` opens its
own DB session, so calling it before the commit would read stale
pre-commit data across that separate connection. Verified with a test
specifically targeting that periodic-commit-vs-final-commit boundary.

### 2026-09-21 — cmc_aster_listed; replace semantics; detail_pull_failures

Added `cmc_aster_listed` as a third exchange source (CMC exchange slug
`aster-pro` — needed special-casing, unlike Bybit/OKX later). Converted
`cmc_top600`/`cmc_binance_listed`/`cmc_universe` from append-only to
replace semantics, for consistency with the new table. Generalized the
exchange-fetching helper into
`_fetch_cmc_exchange_listed_ids(exchange_slug)` in
`app/criteria/market_universe.py`, reused directly for Bybit/OKX later
with zero new helper code. Chose direct Python function calls
(`build_cmc_universe.run()` called from each pull script) over a DB
trigger for cascading the union rebuild — see "Key design decisions"
above for why. Added `detail_pull_failures` (durable failure log,
`full_detail_pull`'s own run logs proved unreliable for a long
rate-limited run) and `project_name`/`project_url`/`cmc_universe.cmc_url`
denormalized fields.

### Earlier

The market-data reference pipeline's foundation — `cmc_top600`,
`cmc_binance_listed`, `cmc_cg_mapping`, `cmc_field_details`,
`cg_field_details`, `coin_field_contrast`, `gap_details`,
`full_detail_pull`, `build_field_contrast` — was built before this file
existed. See `README.md`'s "Market-data reference pipeline" section
(accurate as of current code) and `git log` for that history; not
individually itemized here since this file starts tracking from
2026-09-21 forward.
