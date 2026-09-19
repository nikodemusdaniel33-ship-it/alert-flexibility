"""Tab 1: snapshot the top-N CMC coins by market cap into Postgres
(`python -m scripts.pull_top600 [--top-n 600]`).

Uses the same key-free CMC listing call as app/criteria/market_universe.py
(MARKET_UNIVERSE_TOP_N / --top-n controls N, default 600). Replaces the
cmc_top600 table wholesale each run -- it's a snapshot, not a history.
"""

import argparse
import logging

from app.config import settings
from app.criteria.market_universe import fetch_cmc_universe
from app.db import SessionLocal, ensure_schema
from app.market_data.models import CmcTop600

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pull_top600")


def run(top_n: int | None = None) -> None:
    top_n = top_n or settings.market_universe_top_n
    ensure_schema()

    log.info("Fetching top-%d CMC coins by market cap...", top_n)
    coins = fetch_cmc_universe(top_n)

    db = SessionLocal()
    try:
        db.query(CmcTop600).delete()
        for coin in coins:
            db.add(
                CmcTop600(
                    cmc_id=str(coin["id"]),
                    name=coin["name"],
                    symbol=coin["symbol"],
                    cmc_rank=coin["cmc_rank"],
                )
            )
        db.commit()
        log.info("cmc_top600: %d coins", len(coins))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=None)
    args = parser.parse_args()
    run(args.top_n)
