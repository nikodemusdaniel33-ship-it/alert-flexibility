"""Import the human-reviewed CMC<->CoinGecko mapping into Postgres
(`python -m scripts.import_cmc_cg_mapping`).

Source: data/cmc_cg_mapping.csv (exported from the reviewed mapping
spreadsheet). Every row is imported regardless of its "Valid" flag --
`valid=False` rows are heuristic matches (market-cap or social-link
disambiguation) pending manual confirmation, kept in the table for
visibility rather than dropped. Downstream consumers (scripts/full_detail_pull.py)
should filter on `valid=True` until a row's flipped after review.

Safe to re-run: upserts by cmc_id.
"""

import csv
import logging

from app.db import SessionLocal, ensure_schema
from app.market_data.models import CmcCgMapping

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("import_cmc_cg_mapping")

CSV_PATH = "data/cmc_cg_mapping.csv"


def _int_or_none(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def _float_or_none(value: str) -> float | None:
    value = (value or "").strip()
    return float(value) if value else None


def _bool(value: str) -> bool:
    return (value or "").strip().lower() in {"true", "yes", "y"}


def run(csv_path: str = CSV_PATH) -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))

        for row in rows:
            cmc_id = row["CMC ID"].strip()
            existing = db.get(CmcCgMapping, cmc_id)
            fields = dict(
                cmc_name=row["CMC Name"],
                cmc_symbol=row["CMC Symbol"],
                cg_id=row["CG ID"] or None,
                cg_name=row["CG Name"] or None,
                cmc_rank=_int_or_none(row["CMC Rank"]),
                on_binance_spot=_bool(row["On Binance Spot"]),
                match_method=row["Match Method"] or None,
                name_similarity=_float_or_none(row["Name Similarity"]),
                match_basis=row["Match Basis"] or None,
                needs_reconfirm=_bool(row["Needs Re-confirm"]),
                reason=row["Reason"] or None,
                valid=_bool(row["Valid"]),
            )
            if existing:
                for key, value in fields.items():
                    setattr(existing, key, value)
            else:
                db.add(CmcCgMapping(cmc_id=cmc_id, **fields))

        db.commit()
        log.info("Imported %d mapping rows from %s", len(rows), csv_path)
    finally:
        db.close()


if __name__ == "__main__":
    run()
