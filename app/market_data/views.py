"""Read-side access to the market-data pipeline's Postgres VIEWs -- not
tables (see scripts/create_coins_with_gaps_view.py and
scripts/create_dashboard_views.py). Deliberately kept on their own
MetaData rather than app.db.Base's: ensure_schema()'s create_all() must
never attempt to CREATE TABLE something that already exists as a VIEW,
and this module has no rows of its own to migrate or validate columns
against -- it only ever reads.
"""

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text, select

_metadata = MetaData()

coins_with_gaps = Table(
    "coins_with_gaps",
    _metadata,
    Column("cmc_id", String(32)),
    Column("cg_id", String(256)),
    Column("name", String(256)),
    Column("symbol", String(32)),
    Column("cmc_rank", Integer),
    Column("cmc_url", String(512)),
    Column("in_top600", Boolean),
    Column("on_binance", Boolean),
    Column("on_aster", Boolean),
    Column("gap_count", Integer),
    Column("social_gap_count", Integer),
    Column("market_data_gap_count", Integer),
    Column("contract_gap_count", Integer),
    Column("explorer_gap_count", Integer),
    Column("tags_gap_count", Integer),
    Column("last_contrasted_at", DateTime(timezone=True)),
)

unmapped_coins = Table(
    "unmapped_coins",
    _metadata,
    Column("cmc_id", String(32)),
    Column("name", String(256)),
    Column("symbol", String(32)),
    Column("cmc_rank", Integer),
    Column("in_top600", Boolean),
    Column("on_binance", Boolean),
    Column("on_aster", Boolean),
)

missing_detail_pulls = Table(
    "missing_detail_pulls",
    _metadata,
    Column("cmc_id", String(32)),
    Column("name", String(256)),
    Column("symbol", String(32)),
    Column("cg_id", String(256)),
    Column("missing_cmc_detail", Boolean),
    Column("missing_cg_detail", Boolean),
)

needs_reconfirm_mappings = Table(
    "needs_reconfirm_mappings",
    _metadata,
    Column("cmc_id", String(32)),
    Column("cmc_name", String(256)),
    Column("cmc_symbol", String(32)),
    Column("cg_id", String(256)),
    Column("cg_name", String(256)),
    Column("match_method", String(64)),
    Column("name_similarity", Float),
    Column("match_basis", Text),
    Column("reason", Text),
)

gap_summary_by_field_type = Table(
    "gap_summary_by_field_type",
    _metadata,
    Column("field_type", String(16)),
    Column("gap_rows", Integer),
    Column("coins_affected", Integer),
)

universe_overview = Table(
    "universe_overview",
    _metadata,
    Column("total_coins", Integer),
    Column("in_top600_count", Integer),
    Column("on_binance_count", Integer),
    Column("on_aster_count", Integer),
    Column("all_three_count", Integer),
    Column("mapped_count", Integer),
)

common_missing_chains = Table(
    "common_missing_chains",
    _metadata,
    Column("chain", String(64)),
    Column("coin_count", Integer),
)


def fetch_coins_with_gaps(db, order_by_gap_count_desc: bool = True):
    """Every row currently in the coins_with_gaps view -- always current
    as of the moment this is called; the view has no cached state of its
    own to go stale."""
    stmt = select(coins_with_gaps)
    if order_by_gap_count_desc:
        stmt = stmt.order_by(coins_with_gaps.c.gap_count.desc())
    return db.execute(stmt).all()


def fetch_unmapped_coins(db):
    """cmc_universe coins with no valid CoinGecko mapping -- upstream of
    coins_with_gaps, which only covers coins that already have a mapping
    and comparison data."""
    return db.execute(select(unmapped_coins).order_by(unmapped_coins.c.cmc_rank)).all()


def fetch_missing_detail_pulls(db):
    """cmc_universe coins full_detail_pull hasn't actually populated yet
    -- missing cmc_field_details entirely, or (for a validly-mapped
    coin) missing cg_field_details."""
    return db.execute(select(missing_detail_pulls)).all()


def fetch_needs_reconfirm_mappings(db):
    """cmc_cg_mapping rows still flagged needs_reconfirm -- the
    human-review backlog for heuristic (non-confident) matches."""
    return db.execute(select(needs_reconfirm_mappings)).all()


def fetch_gap_summary_by_field_type(db):
    """Aggregate gap rows and distinct coins affected, grouped by
    field_type -- a dashboard stat-tile source, not per-coin."""
    return db.execute(select(gap_summary_by_field_type)).all()


def fetch_universe_overview(db):
    """Single-row snapshot: total tracked coins, count per source, how
    many are in all three, how many are CoinGecko-mapped."""
    return db.execute(select(universe_overview)).first()


def fetch_common_missing_chains(db):
    """gap_details' contract-type rows grouped by chain, ordered by how
    many coins are missing a contract on it -- a cross-coin pattern, not
    a per-coin status."""
    return db.execute(select(common_missing_chains)).all()
