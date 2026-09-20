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
  `cmc_binance_listed`, `cmc_field_details`, `cg_field_details`,
  `coin_field_contrast`, `gap_details`.
- `data/cmc_cg_mapping.csv` (+ `data/cmc_cg_unmatched.csv`) — a
  human-reviewed CMC↔CoinGecko mapping export. Its `valid` column marks
  confidently-matched rows (contract address or a unique symbol) versus
  heuristic ones (market-cap or social-link disambiguation) pending manual
  confirmation.
- `python -m scripts.import_cmc_cg_mapping` — loads that CSV into
  `cmc_cg_mapping` (upsert by `cmc_id`; safe to re-run).
- `python -m scripts.pull_top600 [--top-n 600]` — snapshots the current
  top-N CMC coins by market cap into `cmc_top600`. **Append-only**: each
  run inserts a new batch of rows sharing one `fetched_at` timestamp
  rather than replacing the table, so history is kept. Readers wanting
  the current snapshot filter to `MAX(fetched_at)` (see `/market-data`
  in `app/main.py`).
- `python -m scripts.pull_binance_listed` — snapshots CMC-listed coins
  currently on Binance Spot into `cmc_binance_listed`, same append-only
  convention. Reuses names from `cmc_top600`'s *latest* batch where
  possible; run `pull_top600` first for fewer API calls.

Both run daily via a dedicated Railway cron service (`market-data-cron`,
`cronSchedule: 0 2 * * *`, `restartPolicyType: NEVER`) rather than
continuously — Railway only starts its container at the scheduled tick,
not on deploy. The `/market-data` dashboard page always shows the latest
batch, with a "last fetched" timestamp per tab.
- `python -m scripts.full_detail_pull [--limit N]` — for the union of the
  two tables above, resolves each coin's CoinGecko id via `cmc_cg_mapping`
  (only `valid=True` rows are trusted), pulls full CMC + CoinGecko detail
  (the same website/twitter/telegram/reddit/whitepaper fields used for
  gap-checking, plus each side's complete raw API response) into
  `coin_details`. CMC detail is fetched in bulk; CoinGecko has no bulk
  detail endpoint, so that side is one call per coin and is the slow part
  of a full run — use `--limit` while testing.

- `python -m scripts.build_field_contrast [--cmc-id ID]` — for every coin
  with a valid mapping and existing `cmc_field_details`/`cg_field_details`
  rows, computes a contrast snapshot into `coin_field_contrast`: reads
  those two tables only, no API calls. One row per social field (9,
  `field_name`/`cmc_value`/`cg_value` populated) plus one summary row each
  for `field_type` in (`contract`, `explorer`, `tags`) (`cmc_count`/
  `cg_count` populated). `gap` is always set, meaning a different
  comparison depending on `field_type`: for social, `cmc_value` empty AND
  `cg_value` present; for the count rows, `cmc_count < cg_count`. Use
  `--cmc-id` while testing. The same run also writes `gap_details`: for
  every `gap=true` row above except `explorer`, the specific missing
  item(s) — one row per gapped social field; for `contract`, one row per
  CoinGecko contract address that doesn't appear anywhere in CMC's address
  list for that coin (compared by address, not chain slug, since the two
  sources don't always agree on a slug for the same address — a
  slug-only comparison produces false positives, e.g. CoinGecko's
  `bitlayer` vs CMC's unmapped `Bitlayer` falling back to `cmc-bitlayer`);
  for `tags`, one summary row (CoinGecko's tag count) rather than
  per-tag, since there's no address-equivalent id to verify a
  name-similarity match against. `explorer` gaps aren't broken out here
  — URL values aren't reliably comparable across sources the way a
  contract address is.

Run order: `import_cmc_cg_mapping` → `pull_top600` → `pull_binance_listed`
→ `full_detail_pull` → `build_field_contrast`.

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
