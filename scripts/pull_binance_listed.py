"""Tab 2: snapshot CMC-listed coins currently trading on Binance -- spot,
perpetual, and futures markets, unioned and deduplicated by cmc_id -- into
Postgres (`python -m scripts.pull_binance_listed`).

Uses the same key-free CMC exchange-listing call as
app/criteria/market_universe.py. Binance membership comes from CMC's own
exchange data (a coin's CMC id on Binance's market pairs), not Binance's
API -- see that module's docstring for why. Deliberately broader than
fetch_cmc_binance_spot_ids (which the live worker's auto-tracking uses,
spot-only on purpose) -- this reference pipeline wants full coverage,
including coins only tradeable as a perpetual (Binance's perpetual
listings include tokenized-stock contracts like AAPL/ADBE alongside
crypto, all still carrying a normal CMC id).

Names for coins already in cmc_top600's latest batch (run scripts/pull_top600
first for best results) come from that table for free; for the rest --
Binance-listed coins outside the top-N pull -- one extra CMC detail call
each. Append-only, same convention as pull_top600.py: every run inserts a
new batch sharing one fetched_at, rather than replacing the table.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func

from app.criteria.market_universe import fetch_cmc_binance_listed_ids, fetch_cmc_info
from app.db import SessionLocal, ensure_schema
from app.market_data.models import CmcBinanceListed, CmcTop600

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pull_binance_listed")


def run() -> None:
    ensure_schema()

    log.info("Fetching CMC-listed coins currently on Binance (spot + perpetual + futures)...")
    binance_ids = fetch_cmc_binance_listed_ids()
    batch_time = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        latest_top600_at = db.query(func.max(CmcTop600.fetched_at)).scalar()
        top600_by_id = (
            {row.cmc_id: row for row in db.query(CmcTop600).filter(CmcTop600.fetched_at == latest_top600_at)}
            if latest_top600_at
            else {}
        )
        missing_ids = [cid for cid in binance_ids if str(cid) not in top600_by_id]
        if missing_ids:
            log.info("%d coins not in cmc_top600 -- fetching names individually", len(missing_ids))
        info = fetch_cmc_info(missing_ids) if missing_ids else {}

        for cid, meta in binance_ids.items():
            cid_str = str(cid)
            top = top600_by_id.get(cid_str)
            if top:
                name, rank = top.name, top.cmc_rank
            else:
                detail = info.get(cid)
                name = detail["name"] if detail else meta.get("slug")
                rank = None
            db.add(
                CmcBinanceListed(
                    cmc_id=cid_str,
                    name=name,
                    symbol=meta["symbol"],
                    cmc_rank=rank,
                    is_spot=meta["is_spot"],
                    is_perpetual=meta["is_perpetual"],
                    is_futures=meta["is_futures"],
                    fetched_at=batch_time,
                )
            )
        db.commit()
        spot_n = sum(1 for m in binance_ids.values() if m["is_spot"])
        perp_n = sum(1 for m in binance_ids.values() if m["is_perpetual"])
        fut_n = sum(1 for m in binance_ids.values() if m["is_futures"])
        log.info(
            "cmc_binance_listed: %d coins (%d spot, %d perpetual, %d futures) (batch %s)",
            len(binance_ids),
            spot_n,
            perp_n,
            fut_n,
            batch_time.isoformat(),
        )
    finally:
        db.close()


if __name__ == "__main__":
    run()
