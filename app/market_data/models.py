"""Reference-data tables for the market-universe pipeline: which coins
exist (top-600 / Binance-listed), how CMC ids map to CoinGecko ids, and
the full detail pulled for each. Standalone from `projects`/`gaps` for
now -- populated by scripts/pull_top600.py, scripts/pull_binance_listed.py,
scripts/import_cmc_cg_mapping.py and scripts/full_detail_pull.py, not yet
wired into the live alerting worker.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
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


class CoinDetail(Base):
    """Full CMC + CoinGecko detail pulled for a coin (scripts/full_detail_pull.py),
    using the same 9-field social diff + per-chain contract/explorer gap
    logic as the live alerting worker (app/detail_compare.py), so this
    reference table stays consistent with what actually drives Gap
    creation. One row per coin, replaced on each pull (a snapshot, not a
    history -- a full run touches every tracked coin and is comparatively
    slow, so unlike cmc_top600/cmc_binance_listed this isn't append-only)."""

    __tablename__ = "coin_details"

    cmc_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    cg_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(256))
    in_top600: Mapped[bool] = mapped_column(Boolean, default=False)
    on_binance_spot: Mapped[bool] = mapped_column(Boolean, default=False)

    # {field_key: {label, cmc_value, cg_value, differs}} for the 9 social
    # fields -- app.detail_compare.social_diffs()'s output verbatim.
    social_diffs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {chain_key: {"contract_missing": bool, "explorer_missing": bool}} --
    # derived from app.detail_compare.field_checklist()'s contract:/explorer:
    # entries, one entry per chain either side has a contract/explorer for.
    chain_gaps: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Count of True entries across social_diffs + chain_gaps, for sorting
    # /filtering without unpacking the JSON columns.
    gap_count: Mapped[int] = mapped_column(Integer, default=0, index=True)

    cmc_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cmc_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cg_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cg_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
