"""Builds cmc_universe by unioning cmc_top600's, cmc_binance_listed's,
cmc_aster_listed's, cmc_bybit_listed's, and cmc_okx_listed's current
snapshots (`python -m scripts.build_cmc_universe`) -- reads those five
tables only, no CMC API calls of its own. Called automatically at the
end of scripts.pull_top600/pull_binance_listed/pull_aster_listed/
pull_bybit_listed/pull_okx_listed (each of them calls this directly, as
a plain Python function call), so running any one of those five scripts
standalone still keeps cmc_universe current -- no separate trigger or
scheduling needed to keep it in sync. Safe to call redundantly (e.g.
after each of the five pull scripts in the same daily run): it's a full
recompute every time, idempotent, no API calls, sub-second.

One row per CMC id found in any of the five sources: in_top600 /
on_binance / on_aster / on_bybit / on_okx flag which source(s) found it
(on_binance/on_aster/on_bybit/on_okx are true if the id is in that
source under any of spot/perpetual/futures -- see each source table for
the per-category breakdown). name/symbol/cmc_rank/cmc_url are taken in
precedence order cmc_top600 > cmc_binance_listed > cmc_aster_listed >
cmc_bybit_listed > cmc_okx_listed -- cmc_url is derived from whichever
source's slug is available (app.criteria.market_universe.cmc_currency_url),
not fetched, so this script keeps making zero CMC API calls of its own.
Replace semantics, same as coin_field_contrast/gap_details/
cmc_field_details/cg_field_details: every run recomputes the full
universe and replaces the table's contents, so it always holds a single
current snapshot -- no history, no batching. fetched_at is just "when
this snapshot was last built".

A coin absent from all five sources simply has no row afterward (not a
row with every flag false) -- see CmcUniverse's docstring for why. To
keep cmc_field_details/cg_field_details from accumulating orphaned rows
for coins that fall out this way, every run also deletes any of their
rows whose cmc_id/cg_id no longer corresponds to a row in the freshly
rebuilt cmc_universe.
"""

import logging
from datetime import datetime, timezone

from app.criteria.market_universe import cmc_currency_url
from app.db import SessionLocal, ensure_schema
from app.market_data.models import (
    CgFieldDetail,
    CmcAsterListed,
    CmcBinanceListed,
    CmcBybitListed,
    CmcCgMapping,
    CmcFieldDetail,
    CmcOkxListed,
    CmcTop600,
    CmcUniverse,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_cmc_universe")


def run() -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        top600 = {r.cmc_id: r for r in db.query(CmcTop600)}
        binance = {r.cmc_id: r for r in db.query(CmcBinanceListed)}
        aster = {r.cmc_id: r for r in db.query(CmcAsterListed)}
        bybit = {r.cmc_id: r for r in db.query(CmcBybitListed)}
        okx = {r.cmc_id: r for r in db.query(CmcOkxListed)}

        if not top600 and not binance and not aster and not bybit and not okx:
            log.warning(
                "cmc_top600, cmc_binance_listed, cmc_aster_listed, cmc_bybit_listed, "
                "and cmc_okx_listed are all empty -- run those scripts first."
            )
            return

        tracked_cmc_ids = set(top600) | set(binance) | set(aster) | set(bybit) | set(okx)

        fetched_at = datetime.now(timezone.utc)
        rows = []
        for cid in sorted(tracked_cmc_ids):
            t, b, a, by, ok = top600.get(cid), binance.get(cid), aster.get(cid), bybit.get(cid), okx.get(cid)
            source = t or b or a or by or ok  # precedence: top600 > binance > aster > bybit > okx
            slug = (
                (t.slug if t else None)
                or (b.slug if b else None)
                or (a.slug if a else None)
                or (by.slug if by else None)
                or (ok.slug if ok else None)
            )
            cmc_rank = (
                t.cmc_rank
                if t
                else (
                    b.cmc_rank
                    if b
                    else (a.cmc_rank if a else (by.cmc_rank if by else (ok.cmc_rank if ok else None)))
                )
            )
            rows.append(
                CmcUniverse(
                    cmc_id=cid,
                    name=source.name,
                    symbol=source.symbol,
                    cmc_rank=cmc_rank,
                    cmc_url=cmc_currency_url(slug),
                    in_top600=t is not None,
                    on_binance=b is not None,
                    on_aster=a is not None,
                    on_bybit=by is not None,
                    on_okx=ok is not None,
                    fetched_at=fetched_at,
                )
            )

        db.query(CmcUniverse).delete()
        db.add_all(rows)

        pruned_cmc = db.query(CmcFieldDetail).filter(~CmcFieldDetail.cmc_id.in_(tracked_cmc_ids)).delete(synchronize_session=False)
        tracked_cg_ids = {
            m.cg_id
            for m in db.query(CmcCgMapping.cg_id).filter(CmcCgMapping.cmc_id.in_(tracked_cmc_ids), CmcCgMapping.cg_id.isnot(None))
        }
        pruned_cg = db.query(CgFieldDetail).filter(~CgFieldDetail.cg_id.in_(tracked_cg_ids)).delete(synchronize_session=False)

        db.commit()
        log.info(
            "cmc_universe: %d coins (%d top600, %d binance, %d aster, %d bybit, %d okx) (snapshot %s)",
            len(rows),
            len(top600),
            len(binance),
            len(aster),
            len(bybit),
            len(okx),
            fetched_at.isoformat(),
        )
        if pruned_cmc or pruned_cg:
            log.info("pruned orphaned detail rows: %d cmc_field_details, %d cg_field_details", pruned_cmc, pruned_cg)
    finally:
        db.close()


if __name__ == "__main__":
    run()
