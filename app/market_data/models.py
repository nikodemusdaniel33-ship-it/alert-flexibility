"""Reference-data tables for the market-universe pipeline: which coins
exist (top-600 / Binance-listed), how CMC ids map to CoinGecko ids, and
the full detail pulled for each. Standalone from `projects`/`gaps` for
now -- populated by scripts/pull_top600.py, scripts/pull_binance_listed.py,
scripts/build_cmc_universe.py, scripts/import_cmc_cg_mapping.py and
scripts/full_detail_pull.py, not yet wired into the live alerting worker.
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
    """CMC-listed coins currently tradeable on Binance (tab 2) -- spot,
    perpetual, and futures markets, unioned and deduplicated by cmc_id --
    per CMC's own exchange listing data. Broader than the live worker's
    auto-tracking criteria (app.criteria.market_universe.fetch_cmc_binance_spot_ids,
    spot-only on purpose). is_spot/is_perpetual/is_futures identify which
    of the three market-pairs categories a coin was actually found under
    (not mutually exclusive -- most spot coins are also perpetual). Rows
    from before this column existed have all three as NULL: the category
    breakdown wasn't tracked yet, not "found nowhere". One batch of rows
    per run of scripts/pull_binance_listed.py (all rows in a batch share
    the same fetched_at). Append-only history, same latest-batch
    convention as CmcTop600."""

    __tablename__ = "cmc_binance_listed"
    __table_args__ = (Index("ix_cmc_binance_listed_fetched_at_cmc_id", "fetched_at", "cmc_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    cmc_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_spot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_perpetual: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_futures: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CmcUniverse(Base):
    """The single "why is this coin tracked" table (scripts/build_cmc_universe.py):
    one row per CMC id currently tracked by either source, with a flag per
    source. Built by reading cmc_top600's and cmc_binance_listed's latest
    batches and unioning them -- no CMC API calls of its own, no change to
    either source table or /market-data (which keeps reading them
    directly). in_top600 / on_binance_spot flag which source(s) found this
    id; on_binance_spot means "found in cmc_binance_listed" specifically
    -- despite the name, that source (and so this flag) covers Binance
    spot, perpetual, and futures market pairs, not spot alone; kept
    as-is to avoid a schema rename on an already-populated table. cmc_rank
    and name/symbol are taken from cmc_top600 when the id is there
    (canonical), falling back to cmc_binance_listed's copy for a
    Binance-only id. Append-only, same latest-batch convention as
    CmcTop600/CmcBinanceListed -- run after both."""

    __tablename__ = "cmc_universe"
    __table_args__ = (Index("ix_cmc_universe_fetched_at_cmc_id", "fetched_at", "cmc_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    cmc_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_top600: Mapped[bool] = mapped_column(Boolean, default=False)
    on_binance_spot: Mapped[bool] = mapped_column(Boolean, default=False)
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


class GapDetail(Base):
    """Where a CoinFieldContrast gap=true row actually falls short
    (scripts/build_field_contrast.py), built from the same
    cmc_field_details/cg_field_details pass -- no extra API calls.
    field_type is "social" | "contract" | "tags" (not "explorer": URL
    values aren't reliably comparable across the two sources the way a
    contract address is, so explorer gaps aren't broken out here).
      - social: one row per gapped field. missing_item is the field name
        (e.g. "discord"), cg_value is CoinGecko's value.
      - contract: one row per chain CoinGecko lists whose contract
        address (case-insensitive) doesn't appear anywhere in CMC's
        address list for this coin -- compared by address, not by chain
        slug, since the two sources don't always agree on a chain's slug
        for the same address (e.g. CoinGecko's "bitlayer" vs CMC's
        unmapped "Bitlayer" falling back to "cmc-bitlayer") and a
        slug-only comparison produces false positives. missing_item is
        CoinGecko's field_name (slug) for that chain, cg_value is the
        address.
      - tags: no per-tag id to verify a name-similarity match against (no
        address-equivalent), so one summary row per coin instead of
        per-tag: missing_item is the literal string "tags", cg_value is
        how many more tags CoinGecko has than CMC (cg_count - cmc_count,
        as a string).
    Snapshot, same replace-per-coin convention as CoinFieldContrast."""

    __tablename__ = "gap_details"
    __table_args__ = (Index("ix_gap_details_cmc_id_type", "cmc_id", "field_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    cg_id: Mapped[str] = mapped_column(String(256), index=True)
    field_type: Mapped[str] = mapped_column(String(16))  # "social" | "contract" | "tags"
    missing_item: Mapped[str] = mapped_column(String(64))
    cg_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
