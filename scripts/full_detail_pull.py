"""Pull full CMC + CoinGecko detail for every coin in cmc_top600 union
cmc_binance_listed (latest batch of each), into the coin_details table
(`python -m scripts.full_detail_pull [--limit N]`).

Uses the same key-free raw-detail fetches and 9-field social diff +
per-chain contract/explorer gap logic as the live alerting worker
(app.criteria.market_universe.fetch_cmc_detail_raw/fetch_cg_detail_raw,
app.detail_compare.social_diffs, app.chain_align.align_chains), so this
reference table stays consistent with what actually drives Gap creation
for tracked projects -- just applied to the whole universe instead of
only the handful of explicitly-tracked coins.

CoinGecko id comes from cmc_cg_mapping (scripts/import_cmc_cg_mapping.py) --
only valid=True rows are trusted; a coin with no valid mapping still gets
its CMC-side detail pulled and stored (symbol/name/raw), with cg_error
noting why the CoinGecko side and the diff columns were skipped.

Neither side has a bulk detail endpoint for this -- CMC's public detail API
and CoinGecko's /coins/{id} are both one-request-per-coin, paced by
retry-with-backoff on rate limits. Expect a full 600-1000 coin run to take
several minutes; use --limit while testing. Rows are committed every
PROGRESS_EVERY coins so a run that dies partway still keeps its progress.
"""

import argparse
import logging
import time

import requests
from sqlalchemy import func

from app.chain_align import align_chains
from app.criteria.market_universe import (
    CMC_DETAIL_REQUEST_DELAY_SECONDS,
    fetch_cg_detail_raw,
    fetch_cmc_detail_raw,
)
from app.db import SessionLocal, ensure_schema
from app.detail_compare import social_diffs as compute_social_diffs
from app.market_data.models import CmcBinanceListed, CmcCgMapping, CmcTop600, CoinDetail

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("full_detail_pull")

PROGRESS_EVERY = 50


def _universe(db) -> dict[str, dict]:
    """{cmc_id: {symbol, name, in_top600, on_binance_spot}} -- the union of
    both detection tables' LATEST batch, deduplicated by cmc_id."""
    universe: dict[str, dict] = {}

    latest_top600_at = db.query(func.max(CmcTop600.fetched_at)).scalar()
    if latest_top600_at:
        for row in db.query(CmcTop600).filter(CmcTop600.fetched_at == latest_top600_at):
            universe[row.cmc_id] = {
                "symbol": row.symbol,
                "name": row.name,
                "in_top600": True,
                "on_binance_spot": False,
            }

    latest_binance_at = db.query(func.max(CmcBinanceListed.fetched_at)).scalar()
    if latest_binance_at:
        for row in db.query(CmcBinanceListed).filter(CmcBinanceListed.fetched_at == latest_binance_at):
            entry = universe.setdefault(
                row.cmc_id,
                {"symbol": row.symbol, "name": row.name, "in_top600": False, "on_binance_spot": False},
            )
            entry["on_binance_spot"] = True

    return universe


def _chain_gaps(raw_cmc: dict, raw_cg: dict) -> dict[str, dict]:
    """{chain_key: {"contract_missing": bool, "explorer_missing": bool}} --
    same conditions as app.detail_compare.field_checklist's contract:/
    explorer: entries, grouped per chain instead of flattened."""
    gaps: dict[str, dict] = {}
    for chain_key, chain in align_chains(raw_cmc, raw_cg).items():
        entry = {}
        if chain.get("cg_contract"):
            entry["contract_missing"] = not chain.get("cmc_contract")
        if chain.get("cg_explorer_urls"):
            entry["explorer_missing"] = not chain.get("cmc_explorer")
        if entry:
            gaps[chain_key] = entry
    return gaps


def _gap_count(diffs: dict[str, dict], chain_gaps: dict[str, dict]) -> int:
    return sum(1 for d in diffs.values() if d["differs"]) + sum(
        1 for entry in chain_gaps.values() for missing in entry.values() if missing
    )


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

        db.query(CoinDetail).filter(CoinDetail.cmc_id.in_(cmc_ids)).delete(synchronize_session=False)

        for i, cmc_id in enumerate(cmc_ids, start=1):
            coin = universe[cmc_id]
            mapping = mappings.get(cmc_id)

            raw_cmc = None
            cmc_error = None
            try:
                raw_cmc = fetch_cmc_detail_raw(int(cmc_id))
            except (requests.RequestException, ValueError, KeyError) as exc:
                cmc_error = str(exc)

            raw_cg = None
            cg_error = None
            if not mapping:
                cg_error = "no mapping row in cmc_cg_mapping"
            elif not mapping.valid:
                cg_error = f"mapping not marked valid (match_method={mapping.match_method})"
            elif not mapping.cg_id:
                cg_error = "mapping row has no cg_id"
            else:
                try:
                    raw_cg = fetch_cg_detail_raw(mapping.cg_id, market_data=True, community_data=True)
                except requests.RequestException as exc:
                    cg_error = str(exc)

            diffs = None
            chain_gaps = None
            gap_count = 0
            if raw_cmc and raw_cg:
                diffs = compute_social_diffs(raw_cmc, raw_cg)
                chain_gaps = _chain_gaps(raw_cmc, raw_cg)
                gap_count = _gap_count(diffs, chain_gaps)

            db.add(
                CoinDetail(
                    cmc_id=cmc_id,
                    cg_id=mapping.cg_id if mapping else None,
                    symbol=coin["symbol"],
                    name=coin["name"],
                    in_top600=coin["in_top600"],
                    on_binance_spot=coin["on_binance_spot"],
                    social_diffs=diffs,
                    chain_gaps=chain_gaps,
                    gap_count=gap_count,
                    cmc_raw=raw_cmc,
                    cmc_error=cmc_error,
                    cg_raw=raw_cg,
                    cg_error=cg_error,
                )
            )

            if i < len(cmc_ids):
                time.sleep(CMC_DETAIL_REQUEST_DELAY_SECONDS)

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
