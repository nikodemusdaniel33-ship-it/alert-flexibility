"""Reference-data tables for the market-universe pipeline: which coins
exist (top-600 / Binance-listed / Aster-listed / Bybit-listed /
OKX-listed), how CMC ids map to CoinGecko ids, and the full detail pulled
for each. Standalone from `projects`/`gaps` for now -- populated by
scripts/pull_top600.py, scripts/pull_binance_listed.py,
scripts/pull_aster_listed.py, scripts/pull_bybit_listed.py,
scripts/pull_okx_listed.py, scripts/build_cmc_universe.py,
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
    """Top-N CMC coins by market cap (tab 1), scripts/pull_top600.py.
    Replace semantics: every run deletes all existing rows and inserts the
    fresh top-N, so this always holds a single current snapshot -- no
    batch history (until 2026-09-21 this was append-only, one growing
    batch per run; that history is gone as of the conversion, by design).
    fetched_at is just "when this snapshot was last pulled", shared by
    every row in it. `slug` is CMC's own per-coin URL slug, carried
    through from the listing API response purely so
    scripts.build_cmc_universe can derive cmc_url without an API call of
    its own."""

    __tablename__ = "cmc_top600"
    __table_args__ = (Index("ix_cmc_top600_fetched_at_cmc_id", "fetched_at", "cmc_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(256))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    slug: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cmc_rank: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CmcBinanceListed(Base):
    """CMC-listed coins currently tradeable on Binance (tab 2) -- spot,
    perpetual, and futures markets, unioned and deduplicated by cmc_id --
    per CMC's own exchange listing data. Broader than the live worker's
    auto-tracking criteria (app.criteria.market_universe.fetch_cmc_binance_spot_ids,
    spot-only on purpose). is_spot/is_perpetual/is_futures identify which
    of the three market-pairs categories a coin was actually found under
    (not mutually exclusive -- most spot coins are also perpetual).
    Replace semantics, same as CmcTop600: every run deletes all existing
    rows and inserts the fresh union, single current snapshot, no batch
    history (converted from append-only alongside CmcTop600 on
    2026-09-21). `slug` is CMC's own per-coin URL slug (also carried
    through for scripts.build_cmc_universe's cmc_url, same as
    CmcTop600's) -- CMC's market-pairs API already returns it, so this
    costs no extra call either."""

    __tablename__ = "cmc_binance_listed"
    __table_args__ = (Index("ix_cmc_binance_listed_fetched_at_cmc_id", "fetched_at", "cmc_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    slug: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cmc_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_spot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_perpetual: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_futures: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CmcAsterListed(Base):
    """CMC-listed coins currently tradeable on Aster (the DEX, CMC exchange
    slug `aster-pro`) -- same shape and semantics as CmcBinanceListed:
    spot/perpetual/futures market pairs, unioned and deduplicated by
    cmc_id, is_spot/is_perpetual/is_futures flagging which category(ies)
    a coin was found under. Replace semantics (single current snapshot,
    no batch history), same as CmcTop600/CmcBinanceListed -- built as
    replace from the start, this table never was append-only.
    scripts/pull_aster_listed.py populates it; not part of
    MarketUniverseProvider's live auto-tracking (Binance-spot-only, see
    CmcBinanceListed's docstring) -- purely a third source feeding
    cmc_universe."""

    __tablename__ = "cmc_aster_listed"
    __table_args__ = (Index("ix_cmc_aster_listed_fetched_at_cmc_id", "fetched_at", "cmc_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    slug: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cmc_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_spot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_perpetual: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_futures: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CmcBybitListed(Base):
    """CMC-listed coins currently tradeable on Bybit -- same shape and
    semantics as CmcBinanceListed/CmcAsterListed: spot/perpetual/futures
    market pairs, unioned and deduplicated by cmc_id, is_spot/is_perpetual/
    is_futures flagging which category(ies) a coin was found under.
    Replace semantics (single current snapshot, no batch history).
    scripts/pull_bybit_listed.py populates it; not part of
    MarketUniverseProvider's live auto-tracking -- purely a fourth source
    feeding cmc_universe."""

    __tablename__ = "cmc_bybit_listed"
    __table_args__ = (Index("ix_cmc_bybit_listed_fetched_at_cmc_id", "fetched_at", "cmc_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    slug: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cmc_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_spot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_perpetual: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_futures: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CmcOkxListed(Base):
    """CMC-listed coins currently tradeable on OKX -- same shape and
    semantics as CmcBinanceListed/CmcAsterListed/CmcBybitListed:
    spot/perpetual/futures market pairs, unioned and deduplicated by
    cmc_id, is_spot/is_perpetual/is_futures flagging which category(ies)
    a coin was found under. Replace semantics (single current snapshot,
    no batch history). scripts/pull_okx_listed.py populates it; not part
    of MarketUniverseProvider's live auto-tracking -- purely a fifth
    source feeding cmc_universe."""

    __tablename__ = "cmc_okx_listed"
    __table_args__ = (Index("ix_cmc_okx_listed_fetched_at_cmc_id", "fetched_at", "cmc_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    slug: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cmc_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_spot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_perpetual: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_futures: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CmcUniverse(Base):
    """The single "why is this coin tracked" table (scripts/build_cmc_universe.py):
    one row per CMC id currently tracked by any of five sources, with a
    flag per source. Built by reading cmc_top600's, cmc_binance_listed's,
    cmc_aster_listed's, cmc_bybit_listed's, and cmc_okx_listed's current
    snapshots and unioning them -- no CMC API calls of its own, no change
    to any source table or /market-data (which keeps reading
    cmc_top600/cmc_binance_listed directly).
    in_top600 / on_binance / on_aster / on_bybit / on_okx flag which
    source(s) found this id; on_binance/on_aster/on_bybit/on_okx are each
    true if the id is in that source under ANY of spot/perpetual/futures
    -- deliberately not broken out per-category here the way each
    source's own is_spot/is_perpetual/is_futures are. cmc_rank,
    name/symbol, and slug (used to derive cmc_url below) are taken in
    precedence order cmc_top600 > cmc_binance_listed > cmc_aster_listed >
    cmc_bybit_listed > cmc_okx_listed (top600 canonical, then whichever
    of the other four has the id). cmc_url is CMC's own catalog page for
    the coin (app.criteria.market_universe.cmc_currency_url on that
    slug) -- derived here rather than fetched, so this table keeps
    making zero CMC API calls of its own.

    Replace semantics, same as CoinFieldContrast/GapDetail/
    CmcFieldDetail/CgFieldDetail: every run recomputes the full universe
    and replaces this table's contents, so it always holds a single
    current snapshot, no history. A coin absent from all three sources'
    current snapshots simply has no row here -- there is no "keep the row,
    mark every flag false" state; a coin nothing currently tracks has no
    reason for a row to exist (deliberate: the alternative would make
    full_detail_pull keep spending CMC/CoinGecko API calls on delisted
    coins forever). To keep cmc_field_details/cg_field_details from
    accumulating rows for coins that fall out this way, every
    build_cmc_universe run also deletes any cmc_field_details/
    cg_field_details rows whose cmc_id/cg_id no longer corresponds to a
    row in the freshly rebuilt cmc_universe -- see build_cmc_universe.py.
    fetched_at is just "when this snapshot was last built", not a batch
    key -- run after all three source scripts (or let each of their
    scripts call build_cmc_universe.run() themselves, which is what they
    do now)."""

    __tablename__ = "cmc_universe"
    __table_args__ = (Index("ix_cmc_universe_fetched_at_cmc_id", "fetched_at", "cmc_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    cmc_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cmc_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    in_top600: Mapped[bool] = mapped_column(Boolean, default=False)
    on_binance: Mapped[bool] = mapped_column(Boolean, default=False)
    on_aster: Mapped[bool] = mapped_column(Boolean, default=False)
    on_bybit: Mapped[bool] = mapped_column(Boolean, default=False)
    on_okx: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CmcFieldDetail(Base):
    """Raw CMC field values (scripts/full_detail_pull.py), one row per
    (cmc_id, field_type, field_name) -- e.g. (1975, social, twitter) or
    (1975, contract, ethereum). Independent of any CoinGecko mapping: in
    principle every coin in the tracked universe can have rows here
    regardless of match confidence (current pull still only runs for
    coins with a resolved CG id -- see full_detail_pull.py). Snapshot:
    all of one cmc_id's rows are replaced together on each pull.
    project_name/project_url denormalize the coin's name and CMC catalog
    page onto every row (same value repeated per cmc_id) purely so this
    table is readable/browsable on its own without joining back to
    cmc_universe -- distinct from the field_type="social" field_name=
    "cmc_url" row above, which exists for the field-by-field enumeration
    pattern that build_field_contrast/gap_details read. A cmc_id's rows
    are deleted (not just left stale) once it drops out of cmc_universe
    entirely -- see build_cmc_universe.py, which does that pruning on
    every run."""

    __tablename__ = "cmc_field_details"
    __table_args__ = (Index("ix_cmc_field_details_cmc_id_type_name", "cmc_id", "field_type", "field_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    field_type: Mapped[str] = mapped_column(String(16))  # "social" | "contract" | "explorer"
    field_name: Mapped[str] = mapped_column(String(64))  # e.g. "website", "twitter"; chain slug for contract/explorer
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    project_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CgFieldDetail(Base):
    """Raw CoinGecko field values (scripts/full_detail_pull.py), one row
    per (cg_id, field_type, field_name) -- e.g. (chainlink, social,
    twitter) or (chainlink, contract, ethereum). Only coins with a
    resolved (valid=True) CoinGecko id get rows here -- fetching needs to
    know which CoinGecko coin to call. Snapshot, same convention as
    CmcFieldDetail. Comparing the two tables (join on cmc_cg_mapping) is
    left to the reader/query rather than precomputed and stored.
    project_name/project_url denormalize the coin's CoinGecko name and
    catalog page onto every row, same rationale and same-value-per-cg_id
    convention as CmcFieldDetail's -- CoinGecko has no per-field
    equivalent of CMC's cmc_url row, so this is that side's only place to
    find its own catalog link. A cg_id's rows are deleted (not just left
    stale) once its mapped cmc_id drops out of cmc_universe entirely --
    see build_cmc_universe.py, which does that pruning on every run."""

    __tablename__ = "cg_field_details"
    __table_args__ = (Index("ix_cg_field_details_cg_id_type_name", "cg_id", "field_type", "field_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cg_id: Mapped[str] = mapped_column(String(256), index=True)
    field_type: Mapped[str] = mapped_column(String(16))
    field_name: Mapped[str] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    project_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DetailPullFailure(Base):
    """Durable record of scripts.full_detail_pull failures -- which
    cmc_id's CMC-side fetch, or which cmc_id's CoinGecko-side fetch,
    failed as of the most recent run. Exists because run logs are
    unreliable for this job in practice (a long rate-limited run can
    freeze mid-stream showing stale output even after the process has
    finished -- see README), so a failure only visible in logs is
    effectively lost. Not an append-only history: a coin that fails gets
    its row upserted (delete-then-insert) with the latest reason; a coin
    that succeeds on a later run has its row for that source deleted in
    that same run -- so this table only ever shows CURRENTLY outstanding
    failures, not every failure that ever happened. source is "cmc" or
    "cg"; cg_id is only set for a "cg" row (a "cmc" failure means the CMC
    fetch itself failed, before a cg_id would even matter)."""

    __tablename__ = "detail_pull_failures"
    __table_args__ = (Index("ix_detail_pull_failures_source_cmc_id", "source", "cmc_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(8))  # "cmc" | "cg"
    cmc_id: Mapped[str] = mapped_column(String(32), index=True)
    cg_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CoinFieldContrast(Base):
    """Precomputed CMC-vs-CoinGecko contrast (scripts/build_field_contrast.py),
    built from cmc_field_details/cg_field_details -- no API calls of its
    own. One row per (coin, field) for field_type in (social, market_data)
    -- 9 + 8 rows, field_name/cmc_value/cg_value populated, cmc_count/
    cg_count NULL -- plus one summary row each for field_type in
    (contract, explorer, tags) -- cmc_count/cg_count populated,
    field_name/cmc_value/cg_value NULL. `gap` is always set, but means a
    different comparison depending on field_type:
      - social/market_data: cmc_value is empty AND cg_value is present.
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
    field_type is "social" | "market_data" | "contract" | "tags" (not
    "explorer": URL values aren't reliably comparable across the two
    sources the way a contract address is, so explorer gaps aren't
    broken out here).
      - social/market_data: one row per gapped field. missing_item is the
        field name (e.g. "discord", "market_cap"), cg_value is
        CoinGecko's value.
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
