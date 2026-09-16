# alert-flexibility

Monitors data completeness on CoinMarketCap versus CoinGecko for a tracked
list of projects, and alerts on Telegram when CMC is missing a detail
(website, X/Twitter, Telegram, Reddit, whitepaper) that CoinGecko already
has. Includes a Telegram-login-gated dashboard for reviewing open gaps,
resolving them, or muting them for a duration or until a specific date.

## How tracking works

1. **Criteria providers** (`app/criteria/`) decide which projects are
   tracked. Today there's one provider, `ManualListProvider`, backed by
   `config/projects.yaml`. Tracking criteria isn't limited to CMC/CoinGecko
   data — a future provider can pull from any external source (exchange
   listing APIs, tag/category feeds, etc); add it to
   `app/criteria/registry.py` and nothing else needs to change.
2. Every worker run starts with a **criteria sync**: providers' candidates
   are upserted into `projects`; anything no provider matches anymore is
   deactivated (not deleted, so gap history survives).
3. For each active project, the worker fetches CMC + CoinGecko socials and
   diffs them (`app/compare.py: TRACKED_FIELDS`).
4. Each missing field is tracked as a **gap** (`app/models.py: Gap`) with
   its own lifecycle:
   - `OPEN` — newly detected, or still missing; re-alerts on Telegram every
     `GAP_REMINDER_INTERVAL_HOURS` (default 24h) until resolved or muted.
   - `MUTED` — snoozed until a specific time (a duration like "6h" and an
     explicit "until this date" are the same mechanism: a `muted_until`
     timestamp). Automatically flips back to `OPEN` (with an alert) once
     that time passes.
   - `RESOLVED` — either marked done by a user on the dashboard (recorded:
     who, when, optional note) or auto-resolved when CMC's data catches up.

## Components

- `app/main.py` — FastAPI web app: Telegram-login dashboard (`/`), JSON API
  (`/api/projects`), gap actions (`/gaps/{id}/resolve|mute|reactivate`).
- `app/worker.py` — one-shot check run (criteria sync → fetch → diff → gap
  lifecycle → alert). Intended to run on a schedule (Railway cron), not
  continuously.
- `app/auth.py` — Telegram Login Widget verification + signed session
  cookies. Every gap action is attributed to the logged-in Telegram user;
  since state lives in Postgres, any other logged-in user sees the same
  status immediately.
- `app/clients/` — CoinMarketCap and CoinGecko API clients.
- `app/criteria/` — pluggable project-tracking criteria (see above).
- `config/projects.yaml` — the manual criteria provider's project list.
  **Replace the placeholder entries with your real tracked-project list.**

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
