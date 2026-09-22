"""Read-side access to coins_with_gaps -- a Postgres VIEW, not a table
(see scripts/create_coins_with_gaps_view.py). Deliberately kept on its
own MetaData rather than app.db.Base's: ensure_schema()'s create_all()
must never attempt to CREATE TABLE something that already exists as a
VIEW, and this module has no rows of its own to migrate or validate
columns against -- it only ever reads.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, select

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


def fetch_coins_with_gaps(db, order_by_gap_count_desc: bool = True):
    """Every row currently in the coins_with_gaps view -- always current
    as of the moment this is called; the view has no cached state of its
    own to go stale."""
    stmt = select(coins_with_gaps)
    if order_by_gap_count_desc:
        stmt = stmt.order_by(coins_with_gaps.c.gap_count.desc())
    return db.execute(stmt).all()
