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

Names for coins already in cmc_top600's current snapshot (run
scripts/pull_top600 first for best results) come from that table for
free; for the rest -- Binance-listed coins outside the top-N pull -- one
extra CMC detail call each. Replace semantics, same convention as
pull_top600.py: every run deletes all existing cmc_binance_listed rows
and inserts the fresh union, so the table always holds a single current
snapshot -- no batch history (converted from append-only 2026-09-21).

Calls scripts.build_cmc_universe.run() at the end, same reason as
pull_top600.py.
"""

import logging
from datetime import datetime, timezone

from app.criteria.market_universe import fetch_cmc_binance_listed_ids, fetch_cmc_info
from app.db import SessionLocal, ensure_schema
from app.market_data.models import CmcBinanceListed, CmcTop600
from scripts.build_cmc_universe import run as rebuild_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pull_binance_listed")


def run() -> None:
    ensure_schema()

    log.info("Fetching CMC-listed coins currently on Binance (spot + perpetual + futures)...")
    binance_ids = fetch_cmc_binance_listed_ids()
    fetched_at = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        top600_by_id = {row.cmc_id: row for row in db.query(CmcTop600)}
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
            slug = (top.slug if top else None) or meta.get("slug")
            db.add(
                CmcBinanceListed(
                    cmc_id=cid_str,
                    name=name,
                    symbol=meta["symbol"],
                    slug=slug,
                    cmc_rank=rank,
                    is_spot=meta["is_spot"],
                    is_perpetual=meta["is_perpetual"],
                    is_futures=meta["is_futures"],
                    fetched_at=fetched_at,
                )
            )
        db.commit()
        spot_n = sum(1 for m in binance_ids.values() if m["is_spot"])
        perp_n = sum(1 for m in binance_ids.values() if m["is_perpetual"])
        fut_n = sum(1 for m in binance_ids.values() if m["is_futures"])
        log.info(
            "cmc_binance_listed: %d coins (%d spot, %d perpetual, %d futures) (snapshot %s)",
            len(binance_ids),
            spot_n,
            perp_n,
            fut_n,
            fetched_at.isoformat(),
        )
    finally:
        db.close()

    rebuild_universe()


if __name__ == "__main__":
    run()
