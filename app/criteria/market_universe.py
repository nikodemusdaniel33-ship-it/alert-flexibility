"""Auto-discovers tracked projects from live market data instead of a
hand-maintained list: the current top N CMC coins by market cap, unioned
with every CMC-listed coin that's also currently trading on Binance Spot.

"Constant" checking comes for free from the existing sync-on-every-worker-run
design (see app/worker.py) — this provider just needs to be enabled and it
gets re-evaluated on whatever cron schedule the worker already runs on, so
newly-listed or newly-ranked coins are picked up automatically and delisted
ones drop out (sync.run() deactivates anything no provider matches anymore).

Binance Spot membership comes from CMC's own exchange listing
(cryptocurrency's `id` on Binance's spot market pairs), not Binance's API —
this sidesteps two problems found while building this: api.binance.com 451s
(geo-blocked) from several cloud regions, and matching by ticker symbol
(e.g. "BTC") is ambiguous across CoinGecko's many wrapped/bridged/impostor
listings, whereas CMC's own currency id is unambiguous by construction.
See _fetch_cmc_binance_spot_ids' docstring for why this uses CMC's public
site API instead of the documented Pro API endpoint.

Matching each CMC coin to a CoinGecko id (needed because Gap tracking pulls
socials from both sides) is best-effort, in priority order:
  1. `config/cmc_cg_overrides.yaml` — manual symbol -> coingecko_id pins.
  2. Contract address match, when CMC reports the coin's primary on-chain
     platform and that chain is in CHAIN_SLUG_CMC_TO_CG below.
  3. Exact symbol match, only when exactly one CoinGecko coin has it (a
     shared symbol like "BTC" bridged/wrapped across many chains would be
     ambiguous, so those are skipped rather than guessed).
  4. Market-cap disambiguation for symbols step 3 couldn't resolve: the real
     asset's market cap dwarfs wrapped/bridged/impersonator clones (tested
     live: real bitcoin ~17,000x its largest same-symbol clone), so a
     confidently-dominant candidate is picked; a close call is left unmatched
     rather than guessed. See AMBIGUOUS_MARKET_CAP_CONFIDENCE_RATIO.
Coins that don't resolve even after that are logged, not synced — add them
to the overrides file once you know the right CoinGecko id.
"""

import logging
import time

import requests
import yaml

from app.criteria.base import Candidate
from app.config import settings

log = logging.getLogger("criteria.market_universe")

CMC_LISTINGS_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
CMC_INFO_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"
# CMC does document an exchange-scoped market-pairs endpoint
# (/v1/exchange/market-pairs/latest -- NOT /v2, that path 404s) but it's
# gated to Hobbyist tier and above, so it's unusable on a Basic/free key.
# This uses the same data CMC's own exchange page
# (coinmarketcap.com/exchanges/binance/) calls client-side instead --
# undocumented, but verified during development to return the identical
# coin set as querying Binance's exchangeInfo directly (494/494 match). If
# the CMC plan gets upgraded to Hobbyist+, swap this for the documented v1
# endpoint (confirm its response shape against a live key first).
CMC_PUBLIC_MARKET_PAIRS_URL = "https://api.coinmarketcap.com/data-api/v3/exchange/market-pairs/latest"
COINGECKO_COINS_LIST_URL = "https://api.coingecko.com/api/v3/coins/list"
COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"

# A symbol match with >1 CoinGecko candidate (e.g. "BTC" also matches a
# dozen wrapped/bridged/impersonator tokens) is resolved by market cap: the
# real asset's market cap dwarfs its clones', so if the top candidate's
# market cap is at least this many times the runner-up's, it's picked with
# confidence. Tested live against BTC (bitcoin $1.5T vs. mezo-wrapped-btc
# $88M, ~17,000x) and BNB (binancecoin $96.6B vs. an impersonator $282K,
# ~340,000x) -- real top-of-market coins clear this by orders of magnitude.
AMBIGUOUS_MARKET_CAP_CONFIDENCE_RATIO = 5

BINANCE_SLUG = "binance"

# CMC's `platform.slug` -> CoinGecko's platform/chain key. The two APIs
# name chains differently; only chains listed here get contract-based
# matching; everything else falls back to symbol matching. Extend as
# needed — an unmapped chain isn't an error, just a weaker match.
CHAIN_SLUG_CMC_TO_CG = {
    "ethereum": "ethereum",
    "bnb": "binance-smart-chain",
    "polygon": "polygon-pos",
    "avalanche": "avalanche",
    "solana": "solana",
    "fantom": "fantom",
    "arbitrum": "arbitrum-one",
    "optimism": "optimistic-ethereum",
    "optimism-ethereum": "optimistic-ethereum",
    "near-protocol": "near-protocol",
    "tron": "tron",
    "cronos": "cronos",
    "harmony": "harmony-shard-0",
    "moonbeam": "moonbeam",
    "moonriver": "moonriver",
    "base": "base",
    "algorand": "algorand",
    "kava": "kava",
    "celo": "celo",
    "heco": "huobi-token",
    "gnosis": "xdai",
    "sui": "sui",
    "ton": "the-open-network",
    "zksync": "zksync",
    "linea": "linea",
    "scroll": "scroll",
    "cardano": "cardano",
    "vechain": "vechain",
    "neo": "neo",
    "okb": "okex-chain",
    "osmosis": "osmosis",
    "xdc-network": "xdc-network",
}


def _fetch_cmc_universe(limit: int) -> list[dict]:
    """Top-`limit` CMC coins by market cap, official Pro API (needs a plan
    that includes cryptocurrency/listings/latest -- available on Basic)."""
    resp = requests.get(
        CMC_LISTINGS_URL,
        params={
            "start": 1,
            "limit": min(limit, 5000),
            "sort": "market_cap",
            "sort_dir": "desc",
            "convert": "USD",
            "aux": "platform",
        },
        headers={"X-CMC_PRO_API_KEY": settings.cmc_api_key, "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def _fetch_cmc_info(ids: list[int]) -> dict[int, dict]:
    """Batch-fetch name/symbol/platform for specific CMC ids (the same
    v2/cryptocurrency/info endpoint app/clients/cmc.py uses for socials),
    for Binance-listed coins that fell outside the top-N pull above."""
    out: dict[int, dict] = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        resp = requests.get(
            CMC_INFO_URL,
            params={"id": ",".join(str(x) for x in chunk)},
            headers={"X-CMC_PRO_API_KEY": settings.cmc_api_key, "Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        for cid, entry in resp.json().get("data", {}).items():
            out[int(cid)] = {
                "id": entry["id"],
                "name": entry["name"],
                "symbol": entry["symbol"],
                "platform": entry.get("platform"),
            }
    return out


def _fetch_cmc_binance_spot_ids() -> dict[int, dict]:
    """{cmc_id: {symbol, slug}} for every coin CMC lists as spot-traded on
    Binance, via CMC's public site API (see module-level comment on
    CMC_PUBLIC_MARKET_PAIRS_URL for why not the documented endpoint)."""
    ids: dict[int, dict] = {}
    start = 1
    limit = 1000
    while True:
        resp = requests.get(
            CMC_PUBLIC_MARKET_PAIRS_URL,
            params={"slug": BINANCE_SLUG, "category": "spot", "start": start, "limit": limit, "convert": "USD"},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        batch = data["marketPairs"]
        for pair in batch:
            ids[pair["baseCurrencyId"]] = {"symbol": pair["baseSymbol"], "slug": pair.get("baseCurrencySlug")}
        total = data.get("numMarketPairs", len(batch))
        if len(batch) < limit or start + limit > total:
            break
        start += limit
    return ids


def _get_with_retry(url: str, *, max_retries: int = 5, **kwargs) -> requests.Response:
    """CoinGecko's free tier rate-limits aggressively; a full sync makes
    several heavy calls to it back to back, so 429s are routine, not
    exceptional. Retries with backoff (honoring Retry-After when CoinGecko
    sends one) before giving up."""
    for attempt in range(max_retries + 1):
        resp = requests.get(url, **kwargs)
        if resp.status_code != 429 or attempt == max_retries:
            resp.raise_for_status()
            return resp
        wait = float(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
        log.warning("market_universe: CoinGecko rate-limited (429), retrying in %.0fs", wait)
        time.sleep(wait)
    raise RuntimeError("unreachable")  # loop always returns or raises above


def _fetch_coingecko_index() -> tuple[dict[tuple[str, str], str], dict[str, list[str]]]:
    """Returns (contract_map, symbol_map) built from CoinGecko's full coin
    list: {(chain, contract_lower): cg_id} and {symbol_upper: [cg_id, ...]}.
    """
    headers = {"Accept": "application/json"}
    if settings.coingecko_api_key:
        headers["x-cg-demo-api-key"] = settings.coingecko_api_key

    resp = _get_with_retry(
        COINGECKO_COINS_LIST_URL,
        params={"include_platform": "true"},
        headers=headers,
        timeout=30,
    )

    contract_map: dict[tuple[str, str], str] = {}
    symbol_map: dict[str, list[str]] = {}
    for coin in resp.json():
        symbol_map.setdefault(coin["symbol"].upper(), []).append(coin["id"])
        for chain, address in (coin.get("platforms") or {}).items():
            if chain and address:
                contract_map[(chain, address.lower())] = coin["id"]

    return contract_map, symbol_map


def _fetch_cg_market_caps(ids: list[str]) -> dict[str, float]:
    headers = {"Accept": "application/json"}
    if settings.coingecko_api_key:
        headers["x-cg-demo-api-key"] = settings.coingecko_api_key

    out: dict[str, float] = {}
    for i in range(0, len(ids), 250):
        chunk = ids[i : i + 250]
        resp = _get_with_retry(
            COINGECKO_MARKETS_URL,
            params={"vs_currency": "usd", "ids": ",".join(chunk), "per_page": 250, "page": 1},
            headers=headers,
            timeout=30,
        )
        for entry in resp.json():
            out[entry["id"]] = entry.get("market_cap") or 0
    return out


def _resolve_ambiguous_by_market_cap(
    ambiguous_symbols: set[str], symbol_map: dict[str, list[str]]
) -> dict[str, str]:
    """{symbol: cg_id} for symbols that had >1 CoinGecko candidate, resolved
    by picking the candidate with by far the largest market cap. Leaves a
    symbol out (caller keeps it unmatched) when there's no confident winner
    -- no market data at all, or the top two are close enough that picking
    one would be a guess rather than a real disambiguation."""
    all_candidate_ids = {cid for symbol in ambiguous_symbols for cid in symbol_map.get(symbol, [])}
    if not all_candidate_ids:
        return {}
    market_caps = _fetch_cg_market_caps(list(all_candidate_ids))

    resolved: dict[str, str] = {}
    for symbol in ambiguous_symbols:
        ranked = sorted(symbol_map.get(symbol, []), key=lambda cid: market_caps.get(cid, 0), reverse=True)
        if not ranked:
            continue
        top_cap = market_caps.get(ranked[0], 0)
        if top_cap <= 0:
            continue
        runner_up_cap = market_caps.get(ranked[1], 0) if len(ranked) > 1 else 0
        if runner_up_cap > 0 and top_cap < runner_up_cap * AMBIGUOUS_MARKET_CAP_CONFIDENCE_RATIO:
            continue
        resolved[symbol] = ranked[0]
    return resolved


def _load_overrides(path: str) -> dict[str, str]:
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    return {symbol.upper(): cg_id for symbol, cg_id in (data.get("overrides") or {}).items()}


def _match_coingecko_id(
    coin: dict,
    overrides: dict[str, str],
    contract_map: dict[tuple[str, str], str],
    symbol_map: dict[str, list[str]],
) -> tuple[str | None, str]:
    symbol = coin["symbol"].upper()

    if symbol in overrides:
        return overrides[symbol], "override"

    platform = coin.get("platform")
    if platform:
        cg_chain = CHAIN_SLUG_CMC_TO_CG.get(platform.get("slug"))
        token_address = platform.get("token_address")
        if cg_chain and token_address:
            cg_id = contract_map.get((cg_chain, token_address.lower()))
            if cg_id:
                return cg_id, "contract"

    candidates = symbol_map.get(symbol, [])
    if len(candidates) == 1:
        return candidates[0], "symbol"
    if len(candidates) > 1:
        return None, "ambiguous_symbol"

    return None, "unmatched"


# This provider currently implements one discovery group: top-N-by-market-cap
# union CMC-listed-and-on-Binance-Spot. Future groups (e.g. a different
# top-N cutoff, another exchange, a tag-based cut) should get their own
# `group` value here rather than overloading this one — the dashboard can
# then filter/tab by `criteria_metadata.group`.
GROUP_TOP_N_OR_BINANCE_SPOT = "group_1_top_n_or_binance_spot"


class MarketUniverseProvider:
    name = "market_universe"

    def fetch_candidates(self) -> list[Candidate]:
        top_n = settings.market_universe_top_n
        top_n_coins = _fetch_cmc_universe(top_n)
        top_n_by_id: dict[int, dict] = {coin["id"]: coin for coin in top_n_coins}

        binance_spot_ids = _fetch_cmc_binance_spot_ids()

        missing_ids = [cid for cid in binance_spot_ids if cid not in top_n_by_id]
        extra_info = _fetch_cmc_info(missing_ids) if missing_ids else {}

        overrides = _load_overrides(settings.market_universe_overrides_path)
        contract_map, symbol_map = _fetch_coingecko_index()

        universe: dict[int, dict] = dict(top_n_by_id)
        for cid, info in extra_info.items():
            universe.setdefault(cid, {**info, "cmc_rank": None})

        selected: dict[int, tuple[dict, bool, bool]] = {}
        matches: dict[int, tuple[str, str]] = {}
        ambiguous_symbols: set[str] = set()

        for cid, coin in universe.items():
            in_top_n = cid in top_n_by_id
            on_binance_spot = cid in binance_spot_ids
            if not in_top_n and not on_binance_spot:
                continue
            selected[cid] = (coin, in_top_n, on_binance_spot)

            cg_id, method = _match_coingecko_id(coin, overrides, contract_map, symbol_map)
            if cg_id is not None:
                matches[cid] = (cg_id, method)
            elif method == "ambiguous_symbol":
                ambiguous_symbols.add(coin["symbol"].upper())

        if ambiguous_symbols:
            resolved = _resolve_ambiguous_by_market_cap(ambiguous_symbols, symbol_map)
            for cid, (coin, _, _) in selected.items():
                symbol = coin["symbol"].upper()
                if cid not in matches and symbol in resolved:
                    matches[cid] = (resolved[symbol], "symbol_by_market_cap")

        candidates: list[Candidate] = []
        unmatched: list[tuple[str, str]] = []

        for cid, (coin, in_top_n, on_binance_spot) in selected.items():
            if cid not in matches:
                reason = "ambiguous_symbol" if coin["symbol"].upper() in ambiguous_symbols else "unmatched"
                unmatched.append((coin["symbol"], reason))
                continue

            cg_id, method = matches[cid]
            candidates.append(
                Candidate(
                    symbol=coin["symbol"],
                    name=coin["name"],
                    cmc_id=str(cid),
                    coingecko_id=cg_id,
                    tier=GROUP_TOP_N_OR_BINANCE_SPOT,
                    source=self.name,
                    metadata={
                        "group": GROUP_TOP_N_OR_BINANCE_SPOT,
                        "cmc_rank": coin.get("cmc_rank"),
                        "in_top_n": in_top_n,
                        "on_binance_spot": on_binance_spot,
                        "match_method": method,
                    },
                )
            )

        if unmatched:
            log.warning(
                "market_universe: %d/%d candidate coins had no CoinGecko match "
                "(add them to %s if they should be tracked): %s",
                len(unmatched),
                len(unmatched) + len(candidates),
                settings.market_universe_overrides_path,
                ", ".join(f"{sym}({reason})" for sym, reason in unmatched[:20]),
            )

        return candidates
