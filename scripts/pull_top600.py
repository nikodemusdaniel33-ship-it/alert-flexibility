"""Tab 1: snapshot the top-N CMC coins by market cap into Postgres
(`python -m scripts.pull_top600 [--top-n 600]`).

Uses the same key-free CMC listing call as app/criteria/market_universe.py
(MARKET_UNIVERSE_TOP_N / --top-n controls N, default 600). Replace
semantics: every run deletes all existing cmc_top600 rows and inserts the
fresh top-N, so the table always holds a single current snapshot -- no
batch history (converted from append-only 2026-09-21, alongside
cmc_binance_listed/cmc_aster_listed). Meant to run daily via a Railway
cron service -- see the market-data-cron service's cronSchedule.

Calls scripts.build_cmc_universe.run() at the end, so cmc_universe stays
current even if this script is ever run standalone (outside the daily
chain) -- see that module's docstring for why a direct Python call was
chosen over a DB trigger.
"""

import argparse
import logging
from datetime import datetime, timezone

from app.config import settings
from app.criteria.market_universe import fetch_cmc_universe
from app.db import SessionLocal, ensure_schema
from app.market_data.models import CmcTop600
from scripts.build_cmc_universe import run as rebuild_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pull_top600")


def run(top_n: int | None = None) -> None:
    top_n = top_n or settings.market_universe_top_n
    ensure_schema()

    log.info("Fetching top-%d CMC coins by market cap...", top_n)
    coins = fetch_cmc_universe(top_n)
    fetched_at = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        db.query(CmcTop600).delete()
        for coin in coins:
            db.add(
                CmcTop600(
                    cmc_id=str(coin["id"]),
                    name=coin["name"],
                    symbol=coin["symbol"],
                    slug=coin.get("slug"),
                    cmc_rank=coin["cmc_rank"],
                    fetched_at=fetched_at,
                )
            )
        db.commit()
        log.info("cmc_top600: %d coins (snapshot %s)", len(coins), fetched_at.isoformat())
    finally:
        db.close()

    rebuild_universe()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=None)
    args = parser.parse_args()
    run(args.top_n)
