"""Tab 3: snapshot CMC-listed coins currently trading on Aster (the DEX,
CMC exchange slug `aster-pro`) -- spot, perpetual, and futures markets,
unioned and deduplicated by cmc_id -- into Postgres
(`python -m scripts.pull_aster_listed`).

Mirrors scripts/pull_binance_listed.py exactly, just against Aster
instead of Binance -- see that module's docstring for the shared
reasoning (CMC's own exchange listing data, not the exchange's API;
broader than any live auto-tracking criteria, which doesn't use Aster at
all). Verified live against CMC's exchange-scoped market-pairs endpoint:
63 spot pairs, 620 perpetual, 0 futures as of 2026-09-21 -- a 0-count
category is a normal outcome, not an error.

Names for coins already in cmc_top600's current snapshot come from that
table for free; for the rest -- Aster-listed coins outside the top-N pull
-- one extra CMC detail call each. Replace semantics, same convention as
pull_top600.py/pull_binance_listed.py: every run deletes all existing
cmc_aster_listed rows and inserts the fresh union, so the table always
holds a single current snapshot -- no batch history.

Calls scripts.build_cmc_universe.run() at the end, same reason as the
other two pull scripts.
"""

import logging
from datetime import datetime, timezone

from app.criteria.market_universe import fetch_cmc_aster_listed_ids, fetch_cmc_info
from app.db import SessionLocal, ensure_schema
from app.market_data.models import CmcAsterListed, CmcTop600
from scripts.build_cmc_universe import run as rebuild_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pull_aster_listed")


def run() -> None:
    ensure_schema()

    log.info("Fetching CMC-listed coins currently on Aster (spot + perpetual + futures)...")
    aster_ids = fetch_cmc_aster_listed_ids()
    fetched_at = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        top600_by_id = {row.cmc_id: row for row in db.query(CmcTop600)}
        missing_ids = [cid for cid in aster_ids if str(cid) not in top600_by_id]
        if missing_ids:
            log.info("%d coins not in cmc_top600 -- fetching names individually", len(missing_ids))
        info = fetch_cmc_info(missing_ids) if missing_ids else {}

        db.query(CmcAsterListed).delete()
        for cid, meta in aster_ids.items():
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
                CmcAsterListed(
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
        spot_n = sum(1 for m in aster_ids.values() if m["is_spot"])
        perp_n = sum(1 for m in aster_ids.values() if m["is_perpetual"])
        fut_n = sum(1 for m in aster_ids.values() if m["is_futures"])
        log.info(
            "cmc_aster_listed: %d coins (%d spot, %d perpetual, %d futures) (snapshot %s)",
            len(aster_ids),
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
