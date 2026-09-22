"""Creates (or replaces) 6 Postgres VIEWs for pipeline-health monitoring
and the future dashboard (`python -m scripts.create_dashboard_views`).
Companion to scripts/create_coins_with_gaps_view.py -- kept as a
separate script since these were requested/delivered as their own
batch, same "no rebuild step of its own" reasoning as that one: every
read re-executes its SELECT live, so none of these need to be re-run
after this except if a view's own definition changes.

- `unmapped_coins` -- cmc_universe coins with no valid CoinGecko mapping
  at all (no comparison possible yet). Upstream of coins_with_gaps,
  which only covers coins that already have comparison data.
- `missing_detail_pulls` -- cmc_universe coins full_detail_pull hasn't
  actually populated: missing from cmc_field_details entirely, or
  (for a validly-mapped coin) missing from cg_field_details.
- `needs_reconfirm_mappings` -- cmc_cg_mapping rows still flagged
  needs_reconfirm (heuristic market-cap/social-link matches pending
  human review).
- `gap_summary_by_field_type` -- aggregate gap rows and distinct coins
  affected, grouped by field_type -- a dashboard stat-tile source, not
  per-coin.
- `universe_overview` -- single-row snapshot: total tracked coins, count
  per source, how many are in all three, how many are CoinGecko-mapped.
- `common_missing_chains` -- gap_details' contract-type rows grouped by
  chain, counting how many coins are missing a contract on it -- a
  cross-coin pattern (does CMC systematically lag on one chain), not a
  per-coin status.

Each uses DROP VIEW IF EXISTS + CREATE VIEW (not CREATE OR REPLACE) so
the same script runs unchanged against both Postgres (production) and
SQLite (local/test) -- SQLite has no CREATE OR REPLACE VIEW syntax.
"""

import logging

from sqlalchemy import text

from app.db import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("create_dashboard_views")

VIEWS = {
    "unmapped_coins": """
        SELECT cu.cmc_id, cu.name, cu.symbol, cu.cmc_rank, cu.in_top600, cu.on_binance, cu.on_aster
        FROM cmc_universe cu
        LEFT JOIN cmc_cg_mapping m ON m.cmc_id = cu.cmc_id AND m.valid = TRUE AND m.cg_id IS NOT NULL
        WHERE m.cmc_id IS NULL
    """,
    "missing_detail_pulls": """
        SELECT
            cu.cmc_id,
            cu.name,
            cu.symbol,
            m.cg_id,
            (cfd.cmc_id IS NULL) AS missing_cmc_detail,
            (m.valid = TRUE AND m.cg_id IS NOT NULL AND cgd.cg_id IS NULL) AS missing_cg_detail
        FROM cmc_universe cu
        LEFT JOIN cmc_cg_mapping m ON m.cmc_id = cu.cmc_id
        LEFT JOIN (SELECT DISTINCT cmc_id FROM cmc_field_details) cfd ON cfd.cmc_id = cu.cmc_id
        LEFT JOIN (SELECT DISTINCT cg_id FROM cg_field_details) cgd ON cgd.cg_id = m.cg_id
        WHERE cfd.cmc_id IS NULL
           OR (m.valid = TRUE AND m.cg_id IS NOT NULL AND cgd.cg_id IS NULL)
    """,
    "needs_reconfirm_mappings": """
        SELECT cmc_id, cmc_name, cmc_symbol, cg_id, cg_name, match_method, name_similarity, match_basis, reason
        FROM cmc_cg_mapping
        WHERE needs_reconfirm = TRUE
    """,
    "gap_summary_by_field_type": """
        SELECT field_type, COUNT(*) AS gap_rows, COUNT(DISTINCT cmc_id) AS coins_affected
        FROM coin_field_contrast
        WHERE gap = TRUE
        GROUP BY field_type
    """,
    "universe_overview": """
        SELECT
            COUNT(*) AS total_coins,
            COUNT(*) FILTER (WHERE cu.in_top600) AS in_top600_count,
            COUNT(*) FILTER (WHERE cu.on_binance) AS on_binance_count,
            COUNT(*) FILTER (WHERE cu.on_aster) AS on_aster_count,
            COUNT(*) FILTER (WHERE cu.in_top600 AND cu.on_binance AND cu.on_aster) AS all_three_count,
            COUNT(*) FILTER (WHERE m.cmc_id IS NOT NULL) AS mapped_count
        FROM cmc_universe cu
        LEFT JOIN cmc_cg_mapping m ON m.cmc_id = cu.cmc_id AND m.valid = TRUE AND m.cg_id IS NOT NULL
    """,
    "common_missing_chains": """
        SELECT missing_item AS chain, COUNT(*) AS coin_count
        FROM gap_details
        WHERE field_type = 'contract'
        GROUP BY missing_item
        ORDER BY coin_count DESC
    """,
}


def run() -> None:
    conn = engine.connect()
    try:
        for name, select_sql in VIEWS.items():
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
            conn.execute(text(f"CREATE VIEW {name} AS {select_sql}"))
            log.info("%s view created", name)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    run()
