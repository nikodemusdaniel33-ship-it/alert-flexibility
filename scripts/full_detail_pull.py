"""Pull full CMC + CoinGecko detail for every coin in cmc_top600 union
cmc_binance_listed, into the coin_details table
(`python -m scripts.full_detail_pull [--limit N]`).

Same website/twitter/telegram/reddit/whitepaper fields already used for
gap-checking (app/clients/cmc.py, app/clients/coingecko.py), but this also
keeps each side's full raw API response (those clients normally discard it
once the booleans are extracted).

CoinGecko id comes from cmc_cg_mapping (scripts/import_cmc_cg_mapping.py) --
only valid=True rows are trusted; a coin with no valid mapping still gets
its CMC-side detail pulled, with cg_error noting why the CoinGecko side was
skipped.

CMC detail is pulled in bulk (a handful of calls total, chunked at 100 ids).
CoinGecko has no bulk detail endpoint, so that side is one call per coin,
paced by retry-with-backoff on rate limits -- expect this step to be the
slow part for a full 600+ coin run. Run with --limit while testing.
"""

import argparse
import logging

import requests

from app.clients import cmc, coingecko
from app.db import SessionLocal, ensure_schema
from app.market_data.models import CmcBinanceListed, CmcCgMapping, CmcTop600, CoinDetail

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("full_detail_pull")

PROGRESS_EVERY = 50


def _universe(db) -> dict[str, dict]:
    """{cmc_id: {symbol, name, in_top600, on_binance_spot}} — the union of
    both detection tables, deduplicated by cmc_id."""
    universe: dict[str, dict] = {}
    for row in db.query(CmcTop600).all():
        universe[row.cmc_id] = {
            "symbol": row.symbol,
            "name": row.name,
            "in_top600": True,
            "on_binance_spot": False,
        }
    for row in db.query(CmcBinanceListed).all():
        entry = universe.setdefault(
            row.cmc_id,
            {"symbol": row.symbol, "name": row.name, "in_top600": False, "on_binance_spot": False},
        )
        entry["on_binance_spot"] = True
    return universe


def run(limit: int | None = None) -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        universe = _universe(db)
        if not universe:
            log.warning("cmc_top600 and cmc_binance_listed are both empty -- run those scripts first.")
            return

        cmc_ids = list(universe.keys())
        if limit:
            cmc_ids = cmc_ids[:limit]
        log.info("Pulling full detail for %d coins", len(cmc_ids))

        mappings = {m.cmc_id: m for m in db.query(CmcCgMapping).filter(CmcCgMapping.cmc_id.in_(cmc_ids)).all()}

        log.info("Fetching CMC detail in bulk...")
        try:
            cmc_details = cmc.fetch_socials_bulk(cmc_ids)
        except requests.RequestException as exc:
            log.error("CMC bulk fetch failed entirely: %s", exc)
            cmc_details = {}

        db.query(CoinDetail).filter(CoinDetail.cmc_id.in_(cmc_ids)).delete(synchronize_session=False)

        for i, cmc_id in enumerate(cmc_ids, start=1):
            coin = universe[cmc_id]
            mapping = mappings.get(cmc_id)

            cmc_fields = cmc_details.get(cmc_id)
            cmc_error = None if cmc_fields else "not returned by CMC bulk info (unknown/delisted id?)"

            cg_fields = None
            cg_error = None
            if not mapping:
                cg_error = "no mapping row in cmc_cg_mapping"
            elif not mapping.valid:
                cg_error = f"mapping not marked valid (match_method={mapping.match_method})"
            elif not mapping.cg_id:
                cg_error = "mapping row has no cg_id"
            else:
                try:
                    cg_fields = coingecko.fetch_socials(mapping.cg_id)
                except requests.RequestException as exc:
                    cg_error = str(exc)

            db.add(
                CoinDetail(
                    cmc_id=cmc_id,
                    cg_id=mapping.cg_id if mapping else None,
                    symbol=coin["symbol"],
                    name=coin["name"],
                    in_top600=coin["in_top600"],
                    on_binance_spot=coin["on_binance_spot"],
                    cmc_website=cmc_fields["website"] if cmc_fields else None,
                    cmc_twitter=cmc_fields["twitter"] if cmc_fields else None,
                    cmc_telegram=cmc_fields["telegram"] if cmc_fields else None,
                    cmc_reddit=cmc_fields["reddit"] if cmc_fields else None,
                    cmc_whitepaper=cmc_fields["whitepaper"] if cmc_fields else None,
                    cmc_raw=cmc_fields["_raw"] if cmc_fields else None,
                    cmc_error=cmc_error,
                    cg_website=cg_fields["website"] if cg_fields else None,
                    cg_twitter=cg_fields["twitter"] if cg_fields else None,
                    cg_telegram=cg_fields["telegram"] if cg_fields else None,
                    cg_reddit=cg_fields["reddit"] if cg_fields else None,
                    cg_whitepaper=cg_fields["whitepaper"] if cg_fields else None,
                    cg_raw=cg_fields["_raw"] if cg_fields else None,
                    cg_error=cg_error,
                )
            )

            if i % PROGRESS_EVERY == 0:
                db.commit()
                log.info("  %d/%d coins pulled", i, len(cmc_ids))

        db.commit()
        log.info("Done: coin_details now has detail for %d coins", len(cmc_ids))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Only pull the first N coins (for testing).")
    args = parser.parse_args()
    run(args.limit)
