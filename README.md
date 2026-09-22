# alert-flexibility

Monitors data completeness on CoinMarketCap versus CoinGecko for a tracked
list of projects, and alerts on Telegram when CMC is behind CoinGecko on a
monitored detail. Includes a Telegram-login-gated dashboard for reviewing
open gaps (resolving them, or muting them for a duration or until a
specific date), plus a full side-by-side CMC-vs-CoinGecko detail page per
project.

## How tracking works

1. **Criteria providers** (`app/criteria/`) decide which projects are
   tracked. `ManualListProvider`, backed by `config/projects.yaml`, always
   runs. `MarketUniverseProvider` (see below) is a second, optional source.
   Tracking criteria isn't limited to CMC/CoinGecko data — a future provider
   can pull from any external source (exchange listing APIs, tag/category
   feeds, etc); add it to `app/criteria/registry.py` and nothing else needs
   to change. All enabled providers' results are unioned by sync.
2. Every worker run starts with a **criteria sync**: providers' candidates
   are upserted into `projects`; anything no provider matches anymore is
   deactivated (not deleted, so gap history survives).
3. For each active project, the worker fetches both sides' full detail
   (`app.criteria.market_universe.fetch_cmc_detail_raw` /
   `fetch_cg_detail_raw` — the same key-free public endpoints
   `MarketUniverseProvider` uses) and runs `app/detail_compare.py`'s
   `field_checklist()`, per the reviewed field spec (see "What gets
   monitored" below).
4. Each checked field is tracked as a **gap** (`app/models.py: Gap`),
   keyed `category:detail` (e.g. `social:twitter`, `contract:ethereum`),
   with its own lifecycle:
   - `OPEN` — newly detected, or still missing/different; re-alerts on
     Telegram every `GAP_REMINDER_INTERVAL_HOURS` (default 24h) until
     resolved or muted.
   - `MUTED` — snoozed until a specific time (a duration like "6h" and an
     explicit "until this date" are the same mechanism: a `muted_until`
     timestamp). Automatically flips back to `OPEN` (with an alert) once
     that time passes.
   - `RESOLVED` — either marked done by a user on the dashboard (recorded:
     who, when, optional note) or auto-resolved when CMC's data catches up.

## What gets monitored

Per the reviewed CMC-vs-CoinGecko field spec (`data/cmc_cg_mapping.csv`'s
sibling analysis, originally hand-built for Synthetix/SNX):

- **Social & link fields** (website, Twitter, Reddit, Discord, whitepaper,
  Github, forum, announcement/blog, Facebook) — a gap when the two sides'
  values are **different**, not just when CMC's is empty. Values are
  normalized first (domain/handle-level, via `app/detail_compare.py`) so
  equivalent formats (a full URL vs. a bare handle, `discord.gg/x` vs.
  `discord.com/invite/x`) don't register as a false gap.
- **Contract address & block explorer, per chain** — a gap per chain
  CoinGecko lists that CMC doesn't (chain alignment is best-effort; see
  `app/chain_align.py`).
- **Identity, market data, supply, and tags/categories** — informational
  only, shown on the per-project detail page, never a gap.

## Components

- `app/main.py` — FastAPI web app: Telegram-login dashboard (`/`), a full
  CMC-vs-CoinGecko detail page per project (`/projects/{id}/detail`), JSON
  API (`/api/projects`), gap actions (`/gaps/{id}/resolve|mute|reactivate`).
- `app/worker.py` — one-shot check run (criteria sync → fetch full detail →
  field checklist → gap lifecycle → alert). Intended to run on a schedule
  (Railway cron), not continuously.
- `app/detail_compare.py` — the social-field diff + per-chain gap checklist
  logic described above.
- `app/chain_align.py` — best-effort CMC/CoinGecko per-chain contract and
  explorer alignment, shared by `app/detail_compare.py` and
  `scripts/compare_coin_detail.py`.
- `app/auth.py` — Telegram Login Widget verification + signed session
  cookies. Every gap action is attributed to the logged-in Telegram user;
  since state lives in Postgres, any other logged-in user sees the same
  status immediately.
- `app/clients/` — CoinMarketCap (Pro API, needs `CMC_API_KEY`) and
  CoinGecko clients; used by `scripts/full_detail_pull.py` and
  `scripts/preview_market_universe.py`'s compare step, not by the live
  worker (which uses the key-free `market_universe.py` fetchers instead).
- `app/criteria/` — pluggable project-tracking criteria (see above).
- `config/projects.yaml` — the manual criteria provider's project list.
  **Replace the placeholder entries with your real tracked-project list.**

## MarketUniverseProvider (auto-tracked coins)

Set `MARKET_UNIVERSE_ENABLED=true` to also track, automatically: the top
`MARKET_UNIVERSE_TOP_N` (default 600) CMC coins by market cap, unioned with
every CMC-listed coin currently trading on Binance Spot (even outside that
top N). It runs alongside `ManualListProvider`, not instead of it, and
re-evaluates on every worker run — newly-ranked or newly-listed coins are
picked up, delisted ones drop out automatically (see "How tracking works"
above). Discovering and mapping this provider's coins needs **no API key
at all** — every CMC and CoinGecko call it makes is public/unauthenticated
(`CMC_API_KEY` is still needed elsewhere, for the per-project socials
comparison once a coin is tracked).

All coins this provider finds currently land in one discovery group,
`group_1_top_n_or_binance_spot` (`Project.tier` / `criteria_metadata.group`)
— a placeholder for when more groups (a different cutoff, another exchange,
a tag-based cut) get added, each as its own group value the dashboard can
eventually filter/tab by.

**Binance Spot listings** come from CMC's own exchange data (a coin's CMC
id on Binance's spot market pairs), not Binance's API directly — this
avoids `api.binance.com` 451ing from several cloud regions (geo-block) and
avoids ambiguous ticker-symbol matching. The CMC top-N listing and per-coin
detail lookups (platform/contract, website, Twitter) also go through CMC's
public site API rather than the documented Pro API endpoints — the
Pro-API-equivalent of the exchange-listing lookup needs a Hobbyist-tier+
key, and using the public API everywhere here keeps one consistent, key-free
code path instead of splitting it across two auth models. Being
undocumented, CMC could change or block this without notice; see the
module docstring in `app/criteria/market_universe.py` for where to swap
back to the documented, key-based Pro API endpoints if that ever happens.

**Matching a CMC coin to its CoinGecko id** (needed since gap-checking
pulls socials from both sides) is layered — manual override, then contract
address (tried against every chain CMC reports for the coin, not just one —
CMC's own listing order isn't priority-ordered; confirmed live against
USDC's 97 chains, where Ethereum was listed 84th), then unique ticker
symbol, then (for symbols CoinGecko lists more than once, e.g. "BTC" also
matching a dozen wrapped/bridged/impersonator tokens) the candidate whose
market cap dominates the runner-up's, then — for the handful left over even
after that — comparing CMC's own website/Twitter against each remaining
candidate's. Stress-tested against the live top 600 + Binance Spot set
(~807 coins, run twice back to back): 98%+ resolved, 0 disagreements
between the two runs (fully deterministic), survived sustained CoinGecko
rate-limiting and transient connection errors without crashing (retry-with-
backoff), and every major coin (BTC/ETH/BNB/SOL/XRP/DOGE/...) landed on the
real `bitcoin`/`ethereum`/`binancecoin`/`solana`/etc., never a clone. Coins
that still can't be resolved are logged as a worker warning, not silently
tracked with a guessed id — add them to `config/cmc_cg_overrides.yaml` once
you know the right CoinGecko id.

CoinGecko's free tier rate-limits fairly aggressively for the social-match
tier specifically (one API call per remaining candidate, no bulk endpoint
for it) — setting `COINGECKO_API_KEY` (a free demo key is enough) raises
those limits and avoids the multi-retry waits seen in testing without one.

CMC's public per-coin detail lookup (used for Binance-listed coins outside
the top N, and for social-match candidates) has no bulk form either — one
request per coin, paced with a small delay — so a sync with many such coins
takes noticeably longer than the near-instant top-N listing call. This is
the tradeoff for not needing a CMC API key; if that ever matters more than
staying key-free, swap it for the Pro API's batched `v2/cryptocurrency/info`
(see the module docstring).

## Market-data reference pipeline (standalone, not yet wired into alerting)

A separate set of tables and scripts for building a reviewed, stable
CMC-to-CoinGecko universe — independent of `projects`/`gaps` for now:

- `app/market_data/models.py` — `cmc_cg_mapping`, `cmc_top600`,
  `cmc_binance_listed`, `cmc_aster_listed`, `cmc_universe`,
  `cmc_field_details`, `cg_field_details`, `coin_field_contrast`,
  `gap_details`, `detail_pull_failures`.
- `data/cmc_cg_mapping.csv` (+ `data/cmc_cg_unmatched.csv`) — a
  human-reviewed CMC↔CoinGecko mapping export. Its `valid` column marks
  confidently-matched rows (contract address or a unique symbol) versus
  heuristic ones (market-cap or social-link disambiguation) pending manual
  confirmation.
- `python -m scripts.import_cmc_cg_mapping` — loads that CSV into
  `cmc_cg_mapping` (upsert by `cmc_id`; safe to re-run).
- `python -m scripts.pull_top600 [--top-n 600]` — snapshots the current
  top-N CMC coins by market cap into `cmc_top600`, including each coin's
  `slug` (CMC's own URL slug, carried through purely so `build_cmc_universe`
  can derive `cmc_url` below with no extra API call). **Replace
  semantics**: every run deletes all existing rows and inserts the fresh
  top-N, so the table always holds a single current snapshot — no batch
  history (converted from append-only 2026-09-21).
- `python -m scripts.pull_binance_listed` — snapshots CMC-listed coins
  currently tradeable on Binance into `cmc_binance_listed` (`slug`
  included, same reason as above), same replace convention. Unions
  spot, perpetual, and futures market pairs, deduplicated by `cmc_id` (a
  coin listed under more than one category still gets exactly one row,
  with `is_spot`/`is_perpetual`/`is_futures` flagging which) — broader on
  purpose than `MarketUniverseProvider`'s live auto-tracking criteria
  above, which stays spot-only (Binance's perpetual listings include
  tokenized-stock contracts like AAPL/ADBE alongside crypto, not
  something to auto-track/alert on). Reuses names from `cmc_top600`'s
  current snapshot where possible; run `pull_top600` first for fewer API
  calls.
- `python -m scripts.pull_aster_listed` — same as `pull_binance_listed`,
  against the Aster DEX instead (CMC exchange slug `aster-pro`). Not used
  by any live auto-tracking, purely a third source feeding `cmc_universe`.
- `python -m scripts.build_cmc_universe` — unions `cmc_top600`'s,
  `cmc_binance_listed`'s, and `cmc_aster_listed`'s current snapshots into
  `cmc_universe`: one row per CMC id tracked by any of the three, with
  `in_top600`/`on_binance`/`on_aster` flags saying why (`on_binance`/
  `on_aster` are true under any of spot/perpetual/futures — see each
  source table's own `is_spot`/`is_perpetual`/`is_futures` for the
  per-category breakdown), plus `cmc_url` (CMC's own catalog page for the
  coin, derived from whichever source's `slug` is available — not
  fetched; precedence `cmc_top600` > `cmc_binance_listed` >
  `cmc_aster_listed` for name/symbol/rank/slug when a coin is in more
  than one). Reads those three tables only, no CMC API calls of its own.
  **Replace semantics**, same as `cmc_field_details`/`cg_field_details`/
  `coin_field_contrast`/`gap_details`: every run recomputes the full
  universe and replaces the table's contents, so it always holds a
  single current snapshot (no batching, no history) — `fetched_at` is
  just "when this snapshot was last built". A coin absent from all three
  sources simply has no row afterward (not a row with every flag false —
  a coin nothing currently tracks has no reason for a row to exist; the
  alternative would make `full_detail_pull` keep spending CMC/CoinGecko
  API calls on delisted coins forever). To match, every run also deletes
  any `cmc_field_details`/`cg_field_details` rows whose `cmc_id`/`cg_id`
  no longer corresponds to a row in the freshly rebuilt `cmc_universe`,
  so those tables don't accumulate orphaned rows for coins that fell out.
  Doesn't replace any source table or `/market-data` (which keeps
  reading `cmc_top600`/`cmc_binance_listed` directly) -- it's a single
  place to answer "is this CMC id currently tracked, and why."

  **Called automatically**, not just via the daily chain: `pull_top600`,
  `pull_binance_listed`, and `pull_aster_listed` each call
  `build_cmc_universe.run()` directly (plain Python function call) at the
  end of their own `run()`, so `cmc_universe` stays current even if one
  of those three is ever run standalone — not just when the daily chain
  completes. Safe to call redundantly (idempotent full recompute,
  sub-second, no API calls) — a normal daily run ends up calling it up to
  four times (once per pull script, plus once explicitly at the end of
  the chain below) and that's fine. A plain Postgres trigger (reacting to
  `INSERT`s on the three source tables) was considered instead but
  rejected: it would need the same union logic reimplemented in
  PL/pgSQL, a second copy that could silently drift from this one —
  calling the existing Python function directly keeps a single
  implementation and zero sync risk.

`pull_top600`, `pull_binance_listed`, and `pull_aster_listed` all run
daily via a dedicated Railway cron service (`market-data-cron`,
`cronSchedule: 0 2 * * *`, `restartPolicyType: NEVER`) rather than
continuously — Railway only starts its container at the scheduled tick,
not on deploy. The `/market-data` dashboard page shows the current
`cmc_top600`/`cmc_binance_listed` snapshot, with a "last fetched"
timestamp per tab.
- `python -m scripts.full_detail_pull [--limit N] [--cmc-id ID]` — pulls
  CMC detail for **every** coin in `cmc_universe`'s current snapshot (no
  CoinGecko id needed for that side) into `cmc_field_details`, and
  additionally pulls CoinGecko detail into `cg_field_details` for coins
  that have a resolved (`valid=True`) CoinGecko id via `cmc_cg_mapping`
  — long/normalized, one row per field (social, market_data, tags,
  contract, explorer). Every row in both tables also carries
  `project_name`/`project_url` (that coin's name and its catalog page on
  this row's own source), denormalized so either table is browsable on
  its own without a join back to `cmc_universe`/`cmc_cg_mapping`.
  `cmc_field_details`'s `social` rows separately still include a
  `cmc_url` field (same URL, row form rather than column form) —
  deliberately excluded from `coin_field_contrast`/`gap_details` since
  there's no CoinGecko counterpart to compare it against. Neither side
  has a bulk detail endpoint for this — CMC's public detail API and
  CoinGecko's `/coins/{id}` are both one request per coin, paced with
  retry-with-backoff; CoinGecko's free tier is the slow part in practice
  (see `COINGECKO_API_KEY` below, which raises the limit substantially).
  Every run replaces each target coin's rows outright — not
  incremental. Use `--cmc-id` for a single coin while testing,
  or `--limit` to cap a run to the first N coins in the universe. A fetch
  failure (either side) is recorded in `detail_pull_failures`
  (`source` = `cmc`/`cg`, `cmc_id`, `reason`) rather than only logged,
  since this job runs long enough that its own run logs have proven
  unreliable after the fact — that table tracks currently outstanding
  failures only, cleared the moment a coin's fetch succeeds again.

  **Also calls `build_field_contrast` automatically**, same pattern as
  `build_cmc_universe` above (direct Python call, not a DB trigger): every
  coin with a resolved `cg_id` gets `build_field_contrast.run(cmc_id=...)`
  called for it right after its `cmc_field_details`/`cg_field_details`
  rows are committed — deferred to just after each periodic commit (not
  inline per coin), since `build_field_contrast` opens its own DB session
  and would otherwise read stale, pre-this-run data across that separate
  connection. This means `coin_field_contrast`/`gap_details` are never
  more than one batch behind, even for a `--limit`/`--cmc-id` run or one
  interrupted partway — no separate manual step needed to keep them
  current relative to whenever `full_detail_pull` last ran. (This does
  *not* put `full_detail_pull` itself on a schedule — it still only runs
  on demand, deliberately, to stay within the CoinGecko API budget; see
  `COINGECKO_API_KEY` below.)

- `python -m scripts.build_field_contrast [--cmc-id ID]` — for every coin
  with a valid mapping and existing `cmc_field_details`/`cg_field_details`
  rows, computes a contrast snapshot into `coin_field_contrast`: reads
  those two tables only, no API calls. One row per social field (9) and
  per market_data field (8, `field_name`/`cmc_value`/`cg_value`
  populated) plus one summary row each for `field_type` in (`contract`,
  `explorer`, `tags`) (`cmc_count`/`cg_count` populated). `gap` is always
  set, meaning a different comparison depending on `field_type`: for
  social/market_data, `cmc_value` empty AND `cg_value` present; for the
  count rows, `cmc_count < cg_count`. Use `--cmc-id` while testing. The
  same run also writes `gap_details`: for every `gap=true` row above
  except `explorer`, the specific missing item(s) — one row per gapped
  social/market_data field; for `contract`, one row per
  CoinGecko contract address that doesn't appear anywhere in CMC's address
  list for that coin (compared by address, not chain slug, since the two
  sources don't always agree on a slug for the same address — a
  slug-only comparison produces false positives, e.g. CoinGecko's
  `bitlayer` vs CMC's unmapped `Bitlayer` falling back to `cmc-bitlayer`);
  for `tags`, one summary row (how many more tags CoinGecko has than CMC,
  `cg_count - cmc_count`) rather than
  per-tag, since there's no address-equivalent id to verify a
  name-similarity match against. `explorer` gaps aren't broken out here
  — URL values aren't reliably comparable across sources the way a
  contract address is.

Run order: `import_cmc_cg_mapping` → `pull_top600` → `pull_binance_listed`
→ `pull_aster_listed` → `full_detail_pull`. Neither `build_cmc_universe`
nor `build_field_contrast` needs a separate step anymore — each of the
three pull scripts calls `build_cmc_universe` automatically at the end of
its own run, and `full_detail_pull` calls `build_field_contrast`
automatically per coin as it goes, so `cmc_universe` is already current
by the time `full_detail_pull` runs, and `coin_field_contrast`/
`gap_details` are already current by the time `full_detail_pull`
finishes.

## Other standalone scripts

- `python -m scripts.preview_market_universe [--top-n N] [--compare-sample N | --compare-all] [--csv out.csv]`
  — standalone preview of `MarketUniverseProvider`'s 3 steps (detect top-N +
  Binance Spot, map to CoinGecko ids, sample-compare CMC vs CoinGecko
  socials) without needing the database or app running. `--compare-sample 0`
  runs detection + mapping only, with no API key needed at all; the compare
  step needs `CMC_API_KEY`.
- `python -m scripts.compare_coin_detail --cmc-id ID [--cg-id ID] [--overrides path.yaml] [--csv out.csv] [--xlsx out.xlsx]`
  — given just a CMC id, maps it to a CoinGecko id (same 5-tier matcher as
  `MarketUniverseProvider`) and prints/exports the full side-by-side field
  comparison for that one coin: identity, market data, supply, per-chain
  contracts and block explorers, socials, CMC-only metadata (holders,
  CertiK, audits, token unlocks, liquidity pools), and matched/unmatched
  tags vs categories. No API key needed. Pass `--cg-id` to skip auto-mapping
  when you already know the right CoinGecko id. Not the same as
  `scripts.full_detail_pull` above: this one is a single-coin CLI
  export/comparison tool, unrelated to the `coin_details` Postgres table.

## Local setup

```
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL, CMC_API_KEY, TELEGRAM_*, SESSION_SECRET
python -m scripts.seed_projects
uvicorn app.main:app --reload
python -m app.worker   # run a check manually
```

## Required environment variables

| Variable | Notes |
| --- | --- |
| `DATABASE_URL` | Postgres connection string (Railway's Postgres plugin provides this). |
| `CMC_API_KEY` | CoinMarketCap Pro API key (needed for `/v2/cryptocurrency/info`). |
| `COINGECKO_API_KEY` | Optional; only needed on a CoinGecko paid/demo plan. |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather. Used both for sending alerts and for verifying the Login Widget. |
| `TELEGRAM_BOT_USERNAME` | The bot's `@username` (without `@`), used by the login widget. |
| `TELEGRAM_CHAT_ID` | Chat/channel id the bot should post alerts to. |
| `SESSION_SECRET` | Random long string to sign session cookies (`python -c "import secrets; print(secrets.token_urlsafe(32))"`). |
| `GAP_REMINDER_INTERVAL_HOURS` | How often to re-alert on a still-open gap. Default 24. |
| `MARKET_UNIVERSE_ENABLED` | Auto-track top-N CMC coins + Binance-Spot-listed coins (see below). Default `false`. |
| `MARKET_UNIVERSE_TOP_N` | Top-N-by-market-cap cutoff for the above. Default 600. |

### Telegram Login Widget setup

Message @BotFather → `/setdomain` → pick your bot → set it to the domain
the `web` service is deployed at (e.g. `web-production-xxxx.up.railway.app`
or a custom domain). Without this, Telegram will refuse to complete login.

## Deploying on Railway

Two services from this repo, sharing one Postgres:

- **web** — start command `python -m scripts.seed_projects && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **worker** — start command `python -m app.worker`, run on a cron schedule
  (e.g. every 6 hours) via Railway's cron trigger on the service.

Set the environment variables above on both services (or as shared
variables), pointing `DATABASE_URL` at the Postgres plugin's connection
string.
