"""Reference-data tables for the market-universe pipeline: which coins
exist (top-600 / Binance-listed), how CMC ids map to CoinGecko ids, and
the full detail pulled for each. Standalone from `projects`/`gaps` for
now -- populated by scripts/pull_top600.py, scripts/pull_binance_listed.py,
scripts/import_cmc_cg_mapping.py and scripts/full_detail_pull.py, not yet
wired into the live alerting worker.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CmcCgMapping(Base):
    """The human-reviewed CMC-id -> CoinGecko-id mapping, imported from
    data/cmc_cg_mapping.csv (scripts/import_cmc_cg_mapping.py). `valid`
    mirrors the spreadsheet's "Valid" column (Y/N) -- only rows with
    valid=True should be trusted as-is; the rest are heuristic guesses
    (market-cap or social-link disambiguation) pending manual confirmation.
    """

    __tablename__ = "cmc_cg_mapping"

    cmc_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    cmc_name: Mapped[str] = mapped_column(String(256))
    cmc_symbol: Mapped[str] = mapped_column(String(32), index=True)
    cg_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cg_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cmc_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_binance_spot: Mapped[bool] = mapped_column(Boolean, default=False)
    match_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_reconfirm: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CmcTop600(Base):
    """Top-N CMC coins by market cap (tab 1), one batch of rows per run of
    scripts/pull_top600.py (all rows in a batch share the same fetched_at).
    Append-only history -- callers wanting the current snapshot must filter
    to the latest fetched_at themselves (see app.main's /market-data)."""

    __tablename__ = "cmc_top600"
    __table_args__ = (Index("ix_cmc_top600_fetched_at_cmc_id", "fetched_at", "cmc_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(256))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    cmc_rank: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CmcBinanceListed(Base):
    """CMC-listed coins currently trading on Binance Spot (tab 2), per
    CMC's own exchange listing data. One batch of rows per run of
    scripts/pull_binance_listed.py (all rows in a batch share the same
    fetched_at). Append-only history, same latest-batch convention as
    CmcTop600."""

    __tablename__ = "cmc_binance_listed"
    __table_args__ = (Index("ix_cmc_binance_listed_fetched_at_cmc_id", "fetched_at", "cmc_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    cmc_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CmcFieldDetail(Base):
    """Raw CMC field values (scripts/full_detail_pull.py), one row per
    (cmc_id, field_type, field_name) -- e.g. (1975, social, twitter) or
    (1975, contract, ethereum). Independent of any CoinGecko mapping: in
    principle every coin in the tracked universe can have rows here
    regardless of match confidence (current pull still only runs for
    coins with a resolved CG id -- see full_detail_pull.py). Snapshot:
    all of one cmc_id's rows are replaced together on each pull."""

    __tablename__ = "cmc_field_details"
    __table_args__ = (Index("ix_cmc_field_details_cmc_id_type_name", "cmc_id", "field_type", "field_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    field_type: Mapped[str] = mapped_column(String(16))  # "social" | "contract" | "explorer"
    field_name: Mapped[str] = mapped_column(String(64))  # e.g. "website", "twitter"; chain slug for contract/explorer
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CgFieldDetail(Base):
    """Raw CoinGecko field values (scripts/full_detail_pull.py), one row
    per (cg_id, field_type, field_name) -- e.g. (chainlink, social,
    twitter) or (chainlink, contract, ethereum). Only coins with a
    resolved (valid=True) CoinGecko id get rows here -- fetching needs to
    know which CoinGecko coin to call. Snapshot, same convention as
    CmcFieldDetail. Comparing the two tables (join on cmc_cg_mapping) is
    left to the reader/query rather than precomputed and stored."""

    __tablename__ = "cg_field_details"
    __table_args__ = (Index("ix_cg_field_details_cg_id_type_name", "cg_id", "field_type", "field_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cg_id: Mapped[str] = mapped_column(String(256), index=True)
    field_type: Mapped[str] = mapped_column(String(16))
    field_name: Mapped[str] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CoinFieldContrast(Base):
    """Precomputed CMC-vs-CoinGecko contrast (scripts/build_field_contrast.py),
    built from cmc_field_details/cg_field_details -- no API calls of its
    own. One row per (coin, social field) -- 9 rows, field_name/cmc_value/
    cg_value populated, cmc_count/cg_count NULL -- plus one summary row
    each for field_type in (contract, explorer, tags) -- cmc_count/cg_count
    populated, field_name/cmc_value/cg_value NULL. `gap` is always set,
    but means a different comparison depending on field_type:
      - social: cmc_value is empty AND cg_value is present.
      - contract/explorer/tags: cmc_count < cg_count.
    Snapshot, same replace-per-coin convention as CmcFieldDetail."""

    __tablename__ = "coin_field_contrast"
    __table_args__ = (Index("ix_coin_field_contrast_cmc_id_type", "cmc_id", "field_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    cg_id: Mapped[str] = mapped_column(String(256), index=True)
    field_type: Mapped[str] = mapped_column(String(16))  # "social" | "contract" | "explorer" | "tags"
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cmc_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    cg_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    cmc_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cg_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gap: Mapped[bool] = mapped_column(Boolean, index=True)
    contrasted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
