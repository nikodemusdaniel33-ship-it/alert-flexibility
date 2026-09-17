"""Standalone preview of the 3-step market-universe pipeline, without
needing the database or FastAPI app running:

  1. Detect the top-N CMC coins by market cap + every CMC-listed coin also
     on Binance Spot -- solely via CMC's API (app/criteria/market_universe.py).
  2. Map each of those to its CoinGecko id (5-tier matching, same module).
  3. Compare CMC vs CoinGecko socials for a sample of the mapped coins,
     using the exact same clients (app/clients/cmc.py, app/clients/coingecko.py)
     and gap logic (app/compare.py) the real worker uses.

Usage:
    python -m scripts.preview_market_universe
    python -m scripts.preview_market_universe --top-n 600 --compare-sample 20
    python -m scripts.preview_market_universe --compare-all --csv out.csv

Requires CMC_API_KEY in the environment/.env (Basic plan is enough --
nothing here needs a paid tier). COINGECKO_API_KEY is optional.
"""

import argparse
import csv
import sys

from app.clients import cmc, coingecko
from app.compare import missing_in_cmc
from app.config import settings
from app.criteria.market_universe import MarketUniverseProvider


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top-n", type=int, default=None, help="Override MARKET_UNIVERSE_TOP_N for this run.")
    parser.add_argument(
        "--compare-sample", type=int, default=20,
        help="How many mapped coins to run the CMC-vs-CoinGecko detail compare on (0 = skip). Default 20.",
    )
    parser.add_argument(
        "--compare-all", action="store_true",
        help="Run the detail compare on every mapped coin instead of a sample (2 extra API calls per coin).",
    )
    parser.add_argument("--csv", type=str, default=None, help="Write the full mapped coin list to this CSV path.")
    args = parser.parse_args()

    if not settings.cmc_api_key:
        print("CMC_API_KEY is not set -- set it in .env or the environment first.", file=sys.stderr)
        sys.exit(1)

    if args.top_n:
        settings.market_universe_top_n = args.top_n

    print(f"Step 1+2: detecting top-{settings.market_universe_top_n} CMC coins + Binance Spot listings, "
          f"mapping to CoinGecko ids...")
    candidates = MarketUniverseProvider().fetch_candidates()
    print(f"  -> {len(candidates)} coins mapped")

    method_counts: dict[str, int] = {}
    for c in candidates:
        m = c.metadata["match_method"]
        method_counts[m] = method_counts.get(m, 0) + 1
    print(f"  match methods: {method_counts}")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "name", "cmc_id", "coingecko_id", "cmc_rank", "on_binance_spot", "match_method"])
            for c in candidates:
                w.writerow(
                    [
                        c.symbol, c.name, c.cmc_id, c.coingecko_id,
                        c.metadata.get("cmc_rank"), c.metadata.get("on_binance_spot"), c.metadata["match_method"],
                    ]
                )
        print(f"  wrote {args.csv}")

    sample = candidates if args.compare_all else candidates[: args.compare_sample]
    if not sample:
        return

    print(f"\nStep 3: comparing CMC vs CoinGecko details for {len(sample)} coin(s)...")
    for c in sample:
        try:
            cmc_fields = cmc.fetch_socials(c.cmc_id)
        except Exception as e:
            print(f"  {c.symbol:8s} CMC fetch failed: {e}")
            continue
        try:
            cg_fields = coingecko.fetch_socials(c.coingecko_id)
        except Exception as e:
            print(f"  {c.symbol:8s} CoinGecko fetch failed: {e}")
            continue

        gaps = missing_in_cmc(cmc_fields, cg_fields)
        status = f"GAPS: {gaps}" if gaps else "OK (no gaps)"
        print(f"  {c.symbol:8s} cmc={c.cmc_id:8s} cg={c.coingecko_id:25s} {status}")


if __name__ == "__main__":
    main()
