"""Builds cmc_universe by unioning cmc_top600's and cmc_binance_listed's
latest batches (`python -m scripts.build_cmc_universe`) -- reads those two
tables only, no CMC API calls of its own. Run after both
scripts.pull_top600 and scripts.pull_binance_listed.

One row per CMC id found in either source: in_top600 / on_binance_spot
flag which source(s) found it. name/symbol/cmc_rank are taken from
cmc_top600 when the id is there (canonical), falling back to
cmc_binance_listed's copy for a Binance-only id. Append-only, same
latest-batch convention as the two source tables -- readers wanting the
current snapshot filter to MAX(fetched_at).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func

from app.db import SessionLocal, ensure_schema
from app.market_data.models import CmcBinanceListed, CmcTop600, CmcUniverse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_cmc_universe")


def run() -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        latest_top600_at = db.query(func.max(CmcTop600.fetched_at)).scalar()
        top600 = {r.cmc_id: r for r in db.query(CmcTop600).filter(CmcTop600.fetched_at == latest_top600_at)} if latest_top600_at else {}

        latest_binance_at = db.query(func.max(CmcBinanceListed.fetched_at)).scalar()
        binance = (
            {r.cmc_id: r for r in db.query(CmcBinanceListed).filter(CmcBinanceListed.fetched_at == latest_binance_at)}
            if latest_binance_at
            else {}
        )

        if not top600 and not binance:
            log.warning("cmc_top600 and cmc_binance_listed are both empty -- run those scripts first.")
            return

        fetched_at = datetime.now(timezone.utc)
        rows = []
        for cid in sorted(set(top600) | set(binance)):
            t, b = top600.get(cid), binance.get(cid)
            source = t or b  # prefer cmc_top600 as canonical for name/symbol/rank
            rows.append(
                CmcUniverse(
                    cmc_id=cid,
                    name=source.name,
                    symbol=source.symbol,
                    cmc_rank=t.cmc_rank if t else (b.cmc_rank if b else None),
                    in_top600=t is not None,
                    on_binance_spot=b is not None,
                    fetched_at=fetched_at,
                )
            )

        db.add_all(rows)
        db.commit()
        log.info(
            "cmc_universe: %d coins (%d top600, %d binance, %d overlap) (batch %s)",
            len(rows),
            len(top600),
            len(binance),
            len(set(top600) & set(binance)),
            fetched_at.isoformat(),
        )
    finally:
        db.close()


if __name__ == "__main__":
    run()
