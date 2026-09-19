"""Tab 2: snapshot CMC-listed coins currently trading on Binance Spot into
Postgres (`python -m scripts.pull_binance_listed`).

Uses the same key-free CMC exchange-listing call as
app/criteria/market_universe.py. Binance Spot membership comes from CMC's
own exchange data (a coin's CMC id on Binance's market pairs), not
Binance's API -- see that module's docstring for why.

Names for coins already in cmc_top600 (run scripts/pull_top600 first for
best results) come from that table for free; for the rest -- Binance-listed
coins outside the top-N pull -- one extra CMC detail call each. Replaces
the cmc_binance_listed table wholesale each run.
"""

import logging

from app.criteria.market_universe import fetch_cmc_binance_spot_ids, fetch_cmc_info
from app.db import SessionLocal, ensure_schema
from app.market_data.models import CmcBinanceListed, CmcTop600

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pull_binance_listed")


def run() -> None:
    ensure_schema()

    log.info("Fetching CMC-listed coins currently on Binance Spot...")
    binance_ids = fetch_cmc_binance_spot_ids()

    db = SessionLocal()
    try:
        top600_by_id = {row.cmc_id: row for row in db.query(CmcTop600).all()}
        missing_ids = [cid for cid in binance_ids if str(cid) not in top600_by_id]
        if missing_ids:
            log.info("%d coins not in cmc_top600 -- fetching names individually", len(missing_ids))
        info = fetch_cmc_info(missing_ids) if missing_ids else {}

        db.query(CmcBinanceListed).delete()
        for cid, meta in binance_ids.items():
            cid_str = str(cid)
            top = top600_by_id.get(cid_str)
            if top:
                name, rank = top.name, top.cmc_rank
            else:
                detail = info.get(cid)
                name = detail["name"] if detail else meta.get("slug")
                rank = None
            db.add(CmcBinanceListed(cmc_id=cid_str, name=name, symbol=meta["symbol"], cmc_rank=rank))
        db.commit()
        log.info("cmc_binance_listed: %d coins", len(binance_ids))
    finally:
        db.close()


if __name__ == "__main__":
    run()
