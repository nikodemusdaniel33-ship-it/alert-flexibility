# alert-flexibility

Monitors data completeness on CoinMarketCap versus CoinGecko for a tracked
list of projects (e.g. T1-exchange listings), and alerts on Telegram when
CMC is missing a detail (website, X/Twitter, Telegram, Reddit, whitepaper)
that CoinGecko already has. Includes a dashboard for reviewing gaps,
assigning a PIC, and muting alerts once a project's data gap is handled.

## Components

- `app/main.py` — FastAPI web app: dashboard (`/`) + JSON API (`/api/projects`).
- `app/worker.py` — one-shot check run (fetch CMC + CG, diff, store, alert).
  Intended to be triggered on a schedule (Railway cron), not run continuously.
- `app/clients/` — CoinMarketCap and CoinGecko API clients.
- `config/projects.yaml` — tracked project list (symbol, CMC id, CoinGecko
  id, tier). **Replace the placeholder entries with your real T1-exchange
  list and criteria.**
- `scripts/seed_projects.py` — upserts `config/projects.yaml` into Postgres.

## Local setup

```
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL, CMC_API_KEY, TELEGRAM_*
python -m scripts.seed_projects
uvicorn app.main:app --reload
python -m app.worker   # run a check manually
```

## Required environment variables

| Variable | Notes |
| --- | --- |
| `DATABASE_URL` | Postgres connection string (Railway's Postgres plugin provides this). |
| `CMC_API_KEY` | CoinMarketCap Pro API key (needed for `/v2/cryptocurrency/info`). |
| `COINGECKO_API_KEY` | Optional; only needed if you're on a CoinGecko paid/demo plan. |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather. |
| `TELEGRAM_CHAT_ID` | Chat/channel id the bot should post alerts to. |

## Deploying on Railway

Two services from this repo, sharing one Postgres:

- **web** — start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **worker** — start command `python -m app.worker`, run on a cron schedule
  (e.g. every 6 hours) via Railway's cron trigger on the service.

Set the environment variables above on both services (or as shared
variables), pointing `DATABASE_URL` at the Postgres plugin's connection
string.
