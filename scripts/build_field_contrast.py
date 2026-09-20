"""Builds coin_field_contrast and gap_details from the already-pulled
cmc_field_details/cg_field_details tables (scripts/full_detail_pull.py) --
no API calls of its own, just a join via cmc_cg_mapping and some counting.

Since full_detail_pull only ever writes a coin's field-detail rows after
BOTH its CMC and CoinGecko fetches succeed in the same run, any cmc_id
with cmc_field_details rows is guaranteed to have matching cg_field_details
rows for its mapped cg_id -- no partial-data cases to handle here.

For each coin with a valid (cmc_cg_mapping.valid=True) mapping and
existing field-detail rows, writes to coin_field_contrast:
  - 9 social rows (one per field_name in SOCIAL_FIELDS) and 8 market_data
    rows (one per field_name in MARKET_DATA_FIELDS): cmc_value, cg_value,
    and gap = cmc_value is empty AND cg_value is present.
  - 1 summary row each for field_type in (contract, explorer, tags):
    cmc_count, cg_count (row counts per field_type), and
    gap = cmc_count < cg_count.

...and, for each gap=true row above (except explorer -- see GapDetail's
docstring), writes the specific missing item(s) to gap_details:
  - social / market_data: the gapped field itself, one row.
  - contract: every CoinGecko contract address that doesn't appear
    anywhere in CMC's address list for this coin -- by address, not by
    chain slug (the two sources don't always agree on a slug for the
    same address; see GapDetail's docstring).
  - tags: one summary row (cg_count minus cmc_count -- how many more tags
    CoinGecko has), not per-tag -- no address-equivalent id to verify a
    name-similarity match against.

Usage:
    python -m scripts.build_field_contrast               # all mapped coins with pulled field detail
    python -m scripts.build_field_contrast --cmc-id 1975  # single coin (testing)
"""

import argparse
import logging
from collections import defaultdict
from datetime import datetime, timezone

from app.db import SessionLocal, ensure_schema
from app.market_data.models import CgFieldDetail, CmcCgMapping, CmcFieldDetail, CoinFieldContrast, GapDetail

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_field_contrast")

SOCIAL_FIELDS = ("website", "twitter", "reddit", "discord", "whitepaper", "forum", "blog", "facebook", "github")
MARKET_DATA_FIELDS = (
    "current_price",
    "price_change_24h",
    "market_cap",
    "volume_24h",
    "circulating_supply",
    "self_reported_circulating_supply",
    "total_supply",
    "max_supply",
)
# Both field_types are per-field, narrow-gap comparisons (cmc empty, cg
# present) -- unlike COUNT_FIELD_TYPES below, which compare row counts.
PER_FIELD_TYPES = {"social": SOCIAL_FIELDS, "market_data": MARKET_DATA_FIELDS}
COUNT_FIELD_TYPES = ("contract", "explorer", "tags")


def _is_present(value: str | None) -> bool:
    return value is not None and value != ""


def _per_field_rows(
    cmc_id: str,
    cg_id: str,
    field_type: str,
    field_names: tuple[str, ...],
    cmc_details: list[CmcFieldDetail],
    cg_details: list[CgFieldDetail],
    now: datetime,
) -> tuple[list[CoinFieldContrast], list[GapDetail]]:
    cmc_values = {d.field_name: d.value for d in cmc_details if d.field_type == field_type}
    cg_values = {d.field_name: d.value for d in cg_details if d.field_type == field_type}

    contrast_rows: list[CoinFieldContrast] = []
    gap_rows: list[GapDetail] = []
    for field_name in field_names:
        cmc_value = cmc_values.get(field_name)
        cg_value = cg_values.get(field_name)
        gap = not _is_present(cmc_value) and _is_present(cg_value)
        contrast_rows.append(
            CoinFieldContrast(
                cmc_id=cmc_id,
                cg_id=cg_id,
                field_type=field_type,
                field_name=field_name,
                cmc_value=cmc_value,
                cg_value=cg_value,
                gap=gap,
                contrasted_at=now,
            )
        )
        if gap:
            gap_rows.append(
                GapDetail(cmc_id=cmc_id, cg_id=cg_id, field_type=field_type, missing_item=field_name, cg_value=cg_value, detected_at=now)
            )
    return contrast_rows, gap_rows


def _build_rows(
    cmc_id: str, cg_id: str, cmc_details: list[CmcFieldDetail], cg_details: list[CgFieldDetail], now: datetime
) -> tuple[list[CoinFieldContrast], list[GapDetail]]:
    contrast_rows: list[CoinFieldContrast] = []
    gap_rows: list[GapDetail] = []

    for field_type, field_names in PER_FIELD_TYPES.items():
        c_rows, g_rows = _per_field_rows(cmc_id, cg_id, field_type, field_names, cmc_details, cg_details, now)
        contrast_rows.extend(c_rows)
        gap_rows.extend(g_rows)

    cmc_counts: dict[str, int] = defaultdict(int)
    for d in cmc_details:
        if d.field_type in COUNT_FIELD_TYPES:
            cmc_counts[d.field_type] += 1
    cg_counts: dict[str, int] = defaultdict(int)
    for d in cg_details:
        if d.field_type in COUNT_FIELD_TYPES:
            cg_counts[d.field_type] += 1

    for field_type in COUNT_FIELD_TYPES:
        cmc_count = cmc_counts[field_type]
        cg_count = cg_counts[field_type]
        gap = cmc_count < cg_count
        contrast_rows.append(
            CoinFieldContrast(
                cmc_id=cmc_id,
                cg_id=cg_id,
                field_type=field_type,
                cmc_count=cmc_count,
                cg_count=cg_count,
                gap=gap,
                contrasted_at=now,
            )
        )
        if not gap:
            continue

        if field_type == "contract":
            cmc_addresses = {d.value.lower() for d in cmc_details if d.field_type == "contract" and d.value}
            for d in cg_details:
                if d.field_type == "contract" and d.value and d.value.lower() not in cmc_addresses:
                    gap_rows.append(
                        GapDetail(cmc_id=cmc_id, cg_id=cg_id, field_type="contract", missing_item=d.field_name, cg_value=d.value, detected_at=now)
                    )
        elif field_type == "tags":
            gap_rows.append(
                GapDetail(cmc_id=cmc_id, cg_id=cg_id, field_type="tags", missing_item="tags", cg_value=str(cg_count - cmc_count), detected_at=now)
            )
        # field_type == "explorer": no gap_details rows -- URL values aren't
        # reliably comparable across sources the way a contract address is.

    return contrast_rows, gap_rows


def run(cmc_id: str | None = None) -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        mapping_q = db.query(CmcCgMapping).filter(CmcCgMapping.valid.is_(True))
        if cmc_id:
            mapping_q = mapping_q.filter(CmcCgMapping.cmc_id == cmc_id)
        mappings = {m.cmc_id: m.cg_id for m in mapping_q.all()}
        if not mappings:
            log.warning("no valid cmc_cg_mapping rows%s -- nothing to contrast.", f" for cmc_id={cmc_id}" if cmc_id else "")
            return

        cmc_ids_with_details = {
            r.cmc_id for r in db.query(CmcFieldDetail.cmc_id).filter(CmcFieldDetail.cmc_id.in_(mappings)).distinct()
        }
        targets = sorted(cmc_ids_with_details)
        skipped = len(mappings) - len(targets)
        log.info("Building contrast for %d coins with pulled field detail (%d skipped, not yet pulled)", len(targets), skipped)

        now = datetime.now(timezone.utc)
        for cid in targets:
            cg_id = mappings[cid]
            cmc_details = db.query(CmcFieldDetail).filter(CmcFieldDetail.cmc_id == cid).all()
            cg_details = db.query(CgFieldDetail).filter(CgFieldDetail.cg_id == cg_id).all()

            db.query(CoinFieldContrast).filter(CoinFieldContrast.cmc_id == cid).delete(synchronize_session=False)
            db.query(GapDetail).filter(GapDetail.cmc_id == cid).delete(synchronize_session=False)
            contrast_rows, gap_rows = _build_rows(cid, cg_id, cmc_details, cg_details, now)
            db.add_all(contrast_rows)
            db.add_all(gap_rows)

        db.commit()
        log.info("Done: coin_field_contrast/gap_details updated for %d coins", len(targets))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cmc-id", type=str, default=None, help="Only build contrast for this one CMC id (for testing).")
    args = parser.parse_args()
    run(args.cmc_id)
