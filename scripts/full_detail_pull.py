"""Pull full CMC + CoinGecko detail for coins in cmc_top600 union
cmc_binance_listed (latest batch of each) that have a resolved (valid=True)
CoinGecko id, into the coin_field_details table
(`python -m scripts.full_detail_pull [--limit N] [--cmc-id ID]`).

Long/normalized format: one row per (cg_id, field_type, field_name) --
e.g. (bitcoin, social, twitter) or (bitcoin, contract, ethereum) -- so a
plain SQL WHERE can filter/sort by field without unpacking JSON. A coin
with no valid CoinGecko mapping gets no rows at all (CG-id-keyed, not
CMC-id-keyed) -- there is nothing CoinGecko-side to compare it against.

Uses the same key-free raw-detail fetches and social diff / per-chain
contract-explorer gap logic as the live alerting worker
(app.criteria.market_universe.fetch_cmc_detail_raw/fetch_cg_detail_raw,
app.detail_compare.social_diffs, app.chain_align.align_chains).

Neither side has a bulk detail endpoint for this -- CMC's public detail API
and CoinGecko's /coins/{id} are both one-request-per-coin, paced by
retry-with-backoff on rate limits. CoinGecko's free tier is the slow part
in practice (see README for COINGECKO_API_KEY, which raises the limit
substantially). Use --cmc-id to pull a single coin while testing, or
--limit to cap a run to the first N coins in the universe.
"""

import argparse
import logging
import time
from datetime import datetime, timezone

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
from app.market_data.models import CmcBinanceListed, CmcCgMapping, CmcTop600, CoinFieldDetail

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("full_detail_pull")

PROGRESS_EVERY = 50


def _universe(db) -> set[str]:
    """CMC ids in the latest batch of cmc_top600 union cmc_binance_listed."""
    universe: set[str] = set()

    latest_top600_at = db.query(func.max(CmcTop600.fetched_at)).scalar()
    if latest_top600_at:
        universe |= {r.cmc_id for r in db.query(CmcTop600.cmc_id).filter(CmcTop600.fetched_at == latest_top600_at)}

    latest_binance_at = db.query(func.max(CmcBinanceListed.fetched_at)).scalar()
    if latest_binance_at:
        universe |= {
            r.cmc_id for r in db.query(CmcBinanceListed.cmc_id).filter(CmcBinanceListed.fetched_at == latest_binance_at)
        }

    return universe


def _as_text(value) -> str | None:
    """A social diff's cmc_value/cg_value is usually a plain string, but
    github's is list-valued (a coin can have several repos) -- render that
    as a comma-joined string instead of Python's list repr."""
    if not value:
        return None
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v) or None
    return str(value)


def _rows_for_coin(cg_id: str, raw_cmc: dict, raw_cg: dict, pulled_at) -> list[CoinFieldDetail]:
    rows = []
    for key, diff in compute_social_diffs(raw_cmc, raw_cg).items():
        rows.append(
            CoinFieldDetail(
                cg_id=cg_id,
                field_type="social",
                field_name=key,
                cmc_value=_as_text(diff["cmc_value"]),
                cg_value=_as_text(diff["cg_value"]),
                differs=diff["differs"],
                pulled_at=pulled_at,
            )
        )

    for chain_key, chain in align_chains(raw_cmc, raw_cg).items():
        if chain.get("cg_contract"):
            rows.append(
                CoinFieldDetail(
                    cg_id=cg_id,
                    field_type="contract",
                    field_name=chain_key,
                    cmc_value=chain.get("cmc_contract"),
                    cg_value=chain.get("cg_contract"),
                    differs=not chain.get("cmc_contract"),
                    pulled_at=pulled_at,
                )
            )
        if chain.get("cg_explorer_urls"):
            rows.append(
                CoinFieldDetail(
                    cg_id=cg_id,
                    field_type="explorer",
                    field_name=chain_key,
                    cmc_value=chain.get("cmc_explorer"),
                    cg_value=", ".join(chain["cg_explorer_urls"]),
                    differs=not chain.get("cmc_explorer"),
                    pulled_at=pulled_at,
                )
            )

    return rows


def run(limit: int | None = None, cmc_id: str | None = None) -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        if cmc_id:
            cmc_ids = [cmc_id]
        else:
            cmc_ids = sorted(_universe(db))
            if not cmc_ids:
                log.warning("cmc_top600 and cmc_binance_listed are both empty -- run those scripts first.")
                return
            if limit:
                cmc_ids = cmc_ids[:limit]

        mappings = {m.cmc_id: m for m in db.query(CmcCgMapping).filter(CmcCgMapping.cmc_id.in_(cmc_ids)).all()}
        targets = [(cid, mappings[cid].cg_id) for cid in cmc_ids if mappings.get(cid) and mappings[cid].valid and mappings[cid].cg_id]
        skipped = len(cmc_ids) - len(targets)
        log.info("Pulling field detail for %d coins with a valid CG mapping (%d skipped, no valid mapping)", len(targets), skipped)

        for i, (cid, cg_id) in enumerate(targets, start=1):
            try:
                raw_cmc = fetch_cmc_detail_raw(int(cid))
            except (requests.RequestException, ValueError, KeyError) as exc:
                log.warning("skipping cmc_id=%s (cg_id=%s): CMC fetch failed (%s)", cid, cg_id, exc)
                if i < len(targets):
                    time.sleep(CMC_DETAIL_REQUEST_DELAY_SECONDS)
                continue

            try:
                raw_cg = fetch_cg_detail_raw(cg_id, market_data=False, community_data=True)
            except requests.RequestException as exc:
                log.warning("skipping cmc_id=%s (cg_id=%s): CoinGecko fetch failed (%s)", cid, cg_id, exc)
                if i < len(targets):
                    time.sleep(CMC_DETAIL_REQUEST_DELAY_SECONDS)
                continue

            pulled_at = datetime.now(timezone.utc)
            rows = _rows_for_coin(cg_id, raw_cmc, raw_cg, pulled_at)

            db.query(CoinFieldDetail).filter(CoinFieldDetail.cg_id == cg_id).delete(synchronize_session=False)
            db.add_all(rows)

            if i < len(targets):
                time.sleep(CMC_DETAIL_REQUEST_DELAY_SECONDS)

            if i % PROGRESS_EVERY == 0:
                db.commit()
                log.info("  %d/%d coins pulled", i, len(targets))

        db.commit()
        log.info("Done: coin_field_details updated for %d coins", len(targets))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Only pull the first N coins (for testing).")
    parser.add_argument("--cmc-id", type=str, default=None, help="Only pull this one CMC id (for testing).")
    args = parser.parse_args()
    run(args.limit, args.cmc_id)
