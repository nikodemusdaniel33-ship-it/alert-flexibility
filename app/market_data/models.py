"""Reference-data tables for the market-universe pipeline: which coins
exist (top-600 / Binance-listed), how CMC ids map to CoinGecko ids, and
the full detail pulled for each. Standalone from `projects`/`gaps` for
now -- populated by scripts/pull_top600.py, scripts/pull_binance_listed.py,
scripts/import_cmc_cg_mapping.py and scripts/full_detail_pull.py, not yet
wired into the live alerting worker.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
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
    """Snapshot of the current top-N CMC coins by market cap (tab 1).
    Replaced wholesale on each run of scripts/pull_top600.py -- this
    table always reflects the latest pull, not a history."""

    __tablename__ = "cmc_top600"

    cmc_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    cmc_rank: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CmcBinanceListed(Base):
    """Snapshot of CMC-listed coins currently trading on Binance Spot
    (tab 2), per CMC's own exchange listing data. Replaced wholesale on
    each run of scripts/pull_binance_listed.py."""

    __tablename__ = "cmc_binance_listed"

    cmc_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    cmc_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CoinDetail(Base):
    """Full CMC + CoinGecko detail pulled for a coin (scripts/full_detail_pull.py):
    the same website/twitter/telegram/reddit/whitepaper booleans used for
    gap-checking elsewhere in the app, plus the complete raw API response
    from each side (app/clients/*.py normally discard the raw payload once
    the booleans are extracted -- this table keeps it). One row per coin,
    replaced on each pull."""

    __tablename__ = "coin_details"

    cmc_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    cg_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(256))
    in_top600: Mapped[bool] = mapped_column(Boolean, default=False)
    on_binance_spot: Mapped[bool] = mapped_column(Boolean, default=False)

    cmc_website: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cmc_twitter: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cmc_telegram: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cmc_reddit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cmc_whitepaper: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cmc_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cmc_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    cg_website: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cg_twitter: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cg_telegram: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cg_reddit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cg_whitepaper: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cg_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cg_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
