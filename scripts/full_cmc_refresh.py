"""Manual, on-demand full CMC-side refresh of the market-data reference
pipeline (`python -m scripts.full_cmc_refresh`) -- NOT on any schedule,
unlike the five pull scripts' own daily chain (`market-data-cron`). Runs
the whole chain in order:

  pull_top600 -> pull_binance_listed -> pull_aster_listed ->
  pull_bybit_listed -> pull_okx_listed -> build_cmc_universe ->
  full_detail_pull(cmc_only=True)

The first five each cascade scripts.build_cmc_universe.run() on their
own already (see that module's docstring), so cmc_universe is current
by the time full_detail_pull runs; the explicit build_cmc_universe call
here is the same belt-and-suspenders redundant call the daily chain
makes, kept for a single clean final `fetched_at` covering every source.

full_detail_pull runs in `--cmc-only` mode: refreshes cmc_field_details
for every coin in the just-rebuilt cmc_universe, and cascades
coin_field_contrast/gap_details for every validly-mapped coin -- but
does NOT touch cg_field_details or make any CoinGecko API call. This
exists specifically so cmc_field_details/coin_field_contrast/gap_details
can be refreshed on demand, as often as wanted, without spending any of
CoinGecko's free-tier budget -- CMC's public API has no equivalent
budget concern (see full_detail_pull.py's docstring). Trigger this
manually (locally, or via the market-data-cron service's startCommand-
hijack pattern -- see ENGINEERING.md) whenever a full CMC-side refresh
is wanted; it is deliberately not wired to any cronSchedule.

For a full run that also refreshes cg_field_details from CoinGecko, use
`python -m scripts.full_detail_pull` directly instead (no --cmc-only).
"""

import logging

from scripts import build_cmc_universe, full_detail_pull, pull_aster_listed, pull_binance_listed, pull_bybit_listed, pull_okx_listed, pull_top600

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("full_cmc_refresh")


def run() -> None:
    log.info("Step 1/6: pull_top600")
    pull_top600.run()
    log.info("Step 2/6: pull_binance_listed")
    pull_binance_listed.run()
    log.info("Step 3/6: pull_aster_listed")
    pull_aster_listed.run()
    log.info("Step 4/6: pull_bybit_listed")
    pull_bybit_listed.run()
    log.info("Step 5/6: pull_okx_listed")
    pull_okx_listed.run()
    build_cmc_universe.run()
    log.info("Step 6/6: full_detail_pull --cmc-only")
    full_detail_pull.run(cmc_only=True)
    log.info("full_cmc_refresh: done")


if __name__ == "__main__":
    run()
