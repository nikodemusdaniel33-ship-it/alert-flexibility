"""Creates (or replaces) the coins_with_gaps Postgres VIEW -- one row per
coin currently in coin_field_contrast with at least one gap=true row,
aggregated with a gap count per field_type
(`python -m scripts.create_coins_with_gaps_view`).

Not a table SQLAlchemy manages via ensure_schema()/create_all() -- a
plain (non-materialized) VIEW is a stored query, not a cached snapshot,
so it needs no rebuild step of its own: every read re-executes the
underlying SELECT against whatever is currently in
coin_field_contrast/cmc_universe at that exact moment. Run this once (or
again only if the view's own definition changes) -- it is deliberately
NOT part of any pipeline chain, since there is no stored state here that
could go stale the way cmc_universe/coin_field_contrast/gap_details can.

Backs the "coins with at least one gap" dashboard list -- see
app/market_data/views.py for the read-side query helper that wraps it.
"""

import logging

from sqlalchemy import text

from app.db import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("create_coins_with_gaps_view")

# DROP + CREATE (not CREATE OR REPLACE) so the same script runs unchanged
# against both Postgres (production) and SQLite (local/test) -- SQLite has
# no CREATE OR REPLACE VIEW syntax.
DROP_VIEW_SQL = "DROP VIEW IF EXISTS coins_with_gaps"

CREATE_VIEW_SQL = """
CREATE VIEW coins_with_gaps AS
SELECT
    cfc.cmc_id,
    cfc.cg_id,
    cu.name,
    cu.symbol,
    cu.cmc_rank,
    cu.cmc_url,
    cu.in_top600,
    cu.on_binance,
    cu.on_aster,
    cu.on_bybit,
    cu.on_okx,
    COUNT(*) FILTER (WHERE cfc.gap) AS gap_count,
    COUNT(*) FILTER (WHERE cfc.gap AND cfc.field_type = 'social') AS social_gap_count,
    COUNT(*) FILTER (WHERE cfc.gap AND cfc.field_type = 'market_data') AS market_data_gap_count,
    COUNT(*) FILTER (WHERE cfc.gap AND cfc.field_type = 'contract') AS contract_gap_count,
    COUNT(*) FILTER (WHERE cfc.gap AND cfc.field_type = 'explorer') AS explorer_gap_count,
    COUNT(*) FILTER (WHERE cfc.gap AND cfc.field_type = 'tags') AS tags_gap_count,
    MAX(cfc.contrasted_at) AS last_contrasted_at
FROM coin_field_contrast cfc
LEFT JOIN cmc_universe cu ON cu.cmc_id = cfc.cmc_id
GROUP BY cfc.cmc_id, cfc.cg_id, cu.name, cu.symbol, cu.cmc_rank, cu.cmc_url, cu.in_top600, cu.on_binance, cu.on_aster, cu.on_bybit, cu.on_okx
HAVING COUNT(*) FILTER (WHERE cfc.gap) > 0
"""


def run() -> None:
    conn = engine.connect()
    try:
        conn.execute(text(DROP_VIEW_SQL))
        conn.execute(text(CREATE_VIEW_SQL))
        conn.commit()
        log.info("coins_with_gaps view created")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
