"""Auto-discovers tracked projects from live market data instead of a
hand-maintained list: the current top N CMC coins by market cap, unioned
with every CMC-listed coin that's also currently trading on Binance Spot.

Every call in this module goes through CMC's or CoinGecko's public,
unauthenticated APIs -- discovering and mapping this provider's coins needs
no API key at all (CMC_API_KEY is still required elsewhere in the app, for
the per-project socials comparison in app/clients/cmc.py). Both CMC calls
here use its public site API (the same data coinmarketcap.com's own pages
call client-side) rather than the documented Pro API, because the
Pro-API-equivalent of the exchange-listing lookup is gated to Hobbyist tier
and above; using the public API for everything here keeps one consistent,
key-free code path instead of splitting it across two auth models. Being
undocumented, CMC could change or block this without notice -- if that ever
happens, `_fetch_cmc_universe`/`_fetch_cmc_info` are where to swap back to
the Pro API (`cryptocurrency/listings/latest` / `v2/cryptocurrency/info`,
both take an `X-CMC_PRO_API_KEY` header and are available on the Basic plan).

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

Matching each CMC coin to a CoinGecko id (needed because Gap tracking pulls
socials from both sides) is best-effort, in priority order:
  1. `config/cmc_cg_overrides.yaml` — manual symbol -> coingecko_id pins.
  2. Contract address match against ANY chain CMC reports for the coin (a
     coin can be deployed on dozens of chains -- CMC's own listing order
     for them isn't priority-ordered, confirmed live against USDC's 97
     platforms, so every one is tried rather than assuming a "primary")
     that's also in CHAIN_SLUG_CMC_TO_CG below.
  3. Exact symbol match, only when exactly one CoinGecko coin has it (a
     shared symbol like "BTC" bridged/wrapped across many chains would be
     ambiguous, so those are skipped rather than guessed).
  4. Market-cap disambiguation for symbols step 3 couldn't resolve: the real
     asset's market cap dwarfs wrapped/bridged/impersonator clones (tested
     live: real bitcoin ~17,000x its largest same-symbol clone), so a
     confidently-dominant candidate is picked; a close call is left unmatched
     rather than guessed. See AMBIGUOUS_MARKET_CAP_CONFIDENCE_RATIO.
  5. Social-link disambiguation for symbols step 4 still couldn't resolve
     (market cap too close to call, e.g. two exchange-issued stablecoins
     sharing a ticker): CMC's own website/Twitter is compared against each
     remaining CoinGecko candidate's, and picked only if exactly one matches.
Coins that don't resolve even after all five tiers are logged, not synced —
add them to the overrides file once you know the right CoinGecko id.
"""

import logging
import re
import time

import requests
import yaml

from app.criteria.base import Candidate
from app.config import settings

log = logging.getLogger("criteria.market_universe")

CMC_LISTING_URL = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
CMC_DETAIL_URL = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/detail"
# CMC does document an exchange-scoped market-pairs endpoint
# (/v1/exchange/market-pairs/latest -- NOT /v2, that path 404s) but it's
# gated to Hobbyist tier and above, so it's unusable on a Basic/free key.
# This uses the same data CMC's own exchange page
# (coinmarketcap.com/exchanges/binance/) calls client-side instead --
# undocumented, but verified during development to return the identical
# coin set as querying Binance's exchangeInfo directly (494/494 match).
CMC_PUBLIC_MARKET_PAIRS_URL = "https://api.coinmarketcap.com/data-api/v3/exchange/market-pairs/latest"
COINGECKO_COINS_LIST_URL = "https://api.coingecko.com/api/v3/coins/list"
COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_COIN_URL = "https://api.coingecko.com/api/v3/coins"

# Pace between the individual per-id calls _fetch_cmc_info makes to
# CMC_DETAIL_URL (that endpoint has no bulk/batch form, unlike the Pro API's
# v2/cryptocurrency/info) -- a plain courtesy delay, not a rate-limit
# response; _get_with_retry still handles 429s/connection hiccups on top.
CMC_DETAIL_REQUEST_DELAY_SECONDS = 0.1

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

# CMC_DETAIL_URL's `platforms[]` entries identify a chain by a human-readable
# `contractPlatform` display name (e.g. "BNB Smart Chain (BEP20)"), not the
# `platform.slug` the listing endpoint uses -- this translates that display
# name (lowercased) into the same slug vocabulary as CHAIN_SLUG_CMC_TO_CG
# above, so _match_coingecko_id doesn't need to know which endpoint a coin
# came from. Verified live against USDC's 97-chain platforms list; entries
# not seen live yet (heco/cardano/vechain/neo/ton) are best-effort guesses
# at CMC's naming convention -- an unmapped one just means a weaker match
# for that coin, not an error.
CMC_DETAIL_PLATFORM_NAME_TO_SLUG = {
    "ethereum": "ethereum",
    "bnb smart chain (bep20)": "bnb",
    "polygon": "polygon",
    "avalanche c-chain": "avalanche",
    "solana": "solana",
    "fantom": "fantom",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "near": "near-protocol",
    "tron20": "tron",
    "cronos": "cronos",
    "harmony": "harmony",
    "moonbeam": "moonbeam",
    "moonriver": "moonriver",
    "base": "base",
    "algorand": "algorand",
    "kava": "kava",
    "celo": "celo",
    "heco": "heco",
    "gnosis chain": "gnosis",
    "sui network": "sui",
    "ton": "ton",
    "zksync era": "zksync",
    "linea": "linea",
    "scroll": "scroll",
    "cardano": "cardano",
    "vechain": "vechain",
    "neo": "neo",
    "okexchain": "okb",
    "osmosis": "osmosis",
    "xdc network": "xdc-network",
}


def _get_with_retry(url: str, *, max_retries: int = 5, **kwargs) -> requests.Response:
    """A full sync makes many calls to CMC and CoinGecko back to back (top-N
    listing, exchange pairs, per-id detail lookups, the full coin index,
    market caps, per-candidate social links...); on both APIs' free/lower
    tiers, 429s are routine, not exceptional, and CMC's public per-id detail
    endpoint (no bulk form, so it's hit far more often) has been seen to drop
    connections with a transient SSL error under load. Retries with backoff
    (honoring Retry-After on a 429 when the API sends one) before giving up."""
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            if attempt == max_retries:
                raise
            wait = 2 ** (attempt + 1)
            log.warning("market_universe: connection error on %s (%s), retrying in %ds", url, exc, wait)
            time.sleep(wait)
            continue

        if resp.status_code != 429 or attempt == max_retries:
            resp.raise_for_status()
            return resp
        wait = float(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
        log.warning("market_universe: rate-limited (429) on %s, retrying in %.0fs", url, wait)
        time.sleep(wait)
    raise RuntimeError("unreachable")  # loop always returns or raises above


def _fetch_cmc_universe(limit: int) -> list[dict]:
    """Top-`limit` CMC coins by market cap, via CMC's public listing API
    (paginated; a single call covers anything up to 5000). Each coin's
    `platform` here is CMC's single "primary" chain for it (id/name/slug/
    token_address) -- fine for most coins, but see `platforms` (plural) in
    _fetch_cmc_info for coins where checking every chain matters more."""
    coins: list[dict] = []
    start = 1
    page_size = 5000
    while start <= limit:
        page_limit = min(page_size, limit - start + 1)
        resp = _get_with_retry(
            CMC_LISTING_URL,
            params={
                "start": start,
                "limit": page_limit,
                "sortBy": "market_cap",
                "sortType": "desc",
                "convert": "USD",
                "cryptoType": "all",
                "tagType": "all",
                "aux": "platform,cmc_rank",
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        batch = resp.json()["data"]["cryptoCurrencyList"]
        if not batch:
            break
        for c in batch:
            coins.append(
                {
                    "id": c["id"],
                    "name": c["name"],
                    "symbol": c["symbol"],
                    "cmc_rank": c.get("cmcRank"),
                    "platform": c.get("platform"),
                }
            )
        start += page_size
    return coins


def _normalize_detail_platforms(platforms: list[dict] | None) -> list[dict]:
    """Turns CMC_DETAIL_URL's `platforms[]` (keyed by a display name, e.g.
    "BNB Smart Chain (BEP20)") into the same {slug, token_address} shape
    _fetch_cmc_universe's `platform` uses, for every chain CMC lists the
    coin on -- not just one, since CMC's own ordering isn't priority-ordered
    (confirmed live: USDC's Ethereum entry was #84 of 97). Entries on a
    chain CMC_DETAIL_PLATFORM_NAME_TO_SLUG doesn't recognize are dropped;
    that's a weaker match for this coin, not an error."""
    out = []
    for p in platforms or []:
        slug = CMC_DETAIL_PLATFORM_NAME_TO_SLUG.get((p.get("contractPlatform") or "").strip().lower())
        address = p.get("contractAddress")
        if slug and address:
            out.append({"slug": slug, "token_address": address})
    return out


def _fetch_cmc_info(ids: list[int]) -> dict[int, dict]:
    """Per-id name/symbol/platforms/urls from CMC's public detail API. Used
    both for Binance-listed coins that fell outside the top-N pull (need
    `platforms` for contract matching) and for coins that reach the
    social-match tier (need `website`/`twitter`; the top-N listing endpoint
    has neither of those). Unlike the Pro API's v2/cryptocurrency/info, this
    endpoint has no bulk form -- one request per id, paced accordingly."""
    out: dict[int, dict] = {}
    for i, cid in enumerate(ids):
        resp = _get_with_retry(
            CMC_DETAIL_URL,
            params={"id": cid},
            headers={"Accept": "application/json"},
            timeout=20,
        )
        entry = resp.json()["data"]
        urls = entry.get("urls") or {}
        out[cid] = {
            "id": entry["id"],
            "name": entry["name"],
            "symbol": entry["symbol"],
            "platforms": _normalize_detail_platforms(entry.get("platforms")),
            "website": (urls.get("website") or [None])[0],
            "twitter": (urls.get("twitter") or [None])[0],
        }
        if i < len(ids) - 1:
            time.sleep(CMC_DETAIL_REQUEST_DELAY_SECONDS)
    return out


def _fetch_cmc_binance_spot_ids() -> dict[int, dict]:
    """{cmc_id: {symbol, slug}} for every coin CMC lists as spot-traded on
    Binance, via CMC's public site API (see module-level comment on
    CMC_PUBLIC_MARKET_PAIRS_URL for why not the documented endpoint)."""
    ids: dict[int, dict] = {}
    start = 1
    limit = 1000
    while True:
        resp = _get_with_retry(
            CMC_PUBLIC_MARKET_PAIRS_URL,
            params={"slug": BINANCE_SLUG, "category": "spot", "start": start, "limit": limit, "convert": "USD"},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        data = resp.json()["data"]
        batch = data["marketPairs"]
        for pair in batch:
            ids[pair["baseCurrencyId"]] = {"symbol": pair["baseSymbol"], "slug": pair.get("baseCurrencySlug")}
        total = data.get("numMarketPairs", len(batch))
        if len(batch) < limit or start + limit > total:
            break
        start += limit
    return ids


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


def _normalize_domain(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip().lower()
    url = re.sub(r"^https?://", "", url)
    if url.startswith("www."):
        url = url[4:]
    url = url.split("/")[0].split("?")[0]
    return url or None


def _normalize_twitter_handle(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    v = re.sub(r"^https?://(www\.)?(twitter|x)\.com/", "", v)
    v = v.strip("/").split("/")[0].split("?")[0]
    return v or None


def _fetch_cg_social_links(ids: list[str]) -> dict[str, dict]:
    """Per-candidate website/twitter from CoinGecko. Unlike market cap,
    there's no bulk endpoint for this -- one call per id -- so this is only
    used on the small set of symbols still ambiguous after the cheaper
    contract/unique-symbol/market-cap tiers."""
    headers = {"Accept": "application/json"}
    if settings.coingecko_api_key:
        headers["x-cg-demo-api-key"] = settings.coingecko_api_key

    out: dict[str, dict] = {}
    for cg_id in ids:
        resp = _get_with_retry(
            f"{COINGECKO_COIN_URL}/{cg_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
            },
            headers=headers,
            timeout=20,
        )
        links = resp.json().get("links", {})
        out[cg_id] = {
            "website_domain": _normalize_domain((links.get("homepage") or [None])[0]),
            "twitter": _normalize_twitter_handle(links.get("twitter_screen_name")),
        }
    return out


def _resolve_ambiguous_by_social_match(
    still_ambiguous: dict[int, dict], symbol_map: dict[str, list[str]]
) -> dict[int, str]:
    """{cmc_id: cg_id} for coins whose symbol survived the market-cap tier
    still ambiguous, resolved by comparing CMC's own website/twitter against
    each CoinGecko candidate's. Picked only when exactly one candidate's
    socials match (by website domain or Twitter handle) -- if socials match
    more than one candidate (e.g. a wrapped token that copies its underlying
    project's links) or none, it's left unmatched rather than guessed."""
    if not still_ambiguous:
        return {}

    cmc_info = _fetch_cmc_info(list(still_ambiguous.keys()))

    all_candidate_ids = {
        cg_id for coin in still_ambiguous.values() for cg_id in symbol_map.get(coin["symbol"].upper(), [])
    }
    cg_socials = _fetch_cg_social_links(list(all_candidate_ids))

    resolved: dict[int, str] = {}
    for cid, coin in still_ambiguous.items():
        cmc = cmc_info.get(cid)
        if not cmc:
            continue
        cmc_domain = _normalize_domain(cmc.get("website"))
        cmc_twitter = _normalize_twitter_handle(cmc.get("twitter"))
        if not cmc_domain and not cmc_twitter:
            continue

        matches = [
            cg_id
            for cg_id in symbol_map.get(coin["symbol"].upper(), [])
            if cg_id in cg_socials
            and (
                (cmc_domain and cg_socials[cg_id]["website_domain"] == cmc_domain)
                or (cmc_twitter and cg_socials[cg_id]["twitter"] == cmc_twitter)
            )
        ]
        if len(matches) == 1:
            resolved[cid] = matches[0]
    return resolved


def _load_overrides(path: str) -> dict[str, str]:
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    return {symbol.upper(): cg_id for symbol, cg_id in (data.get("overrides") or {}).items()}


def _platform_candidates(coin: dict) -> list[dict]:
    """Coins from _fetch_cmc_universe carry a single `platform`; coins from
    _fetch_cmc_info carry a `platforms` list (every chain CMC knows about
    for that coin). Normalized to a list either way so _match_coingecko_id
    can try all of them without caring which endpoint a coin came from."""
    if coin.get("platforms"):
        return coin["platforms"]
    if coin.get("platform"):
        return [coin["platform"]]
    return []


def _match_coingecko_id(
    coin: dict,
    overrides: dict[str, str],
    contract_map: dict[tuple[str, str], str],
    symbol_map: dict[str, list[str]],
) -> tuple[str | None, str]:
    symbol = coin["symbol"].upper()

    if symbol in overrides:
        return overrides[symbol], "override"

    for platform in _platform_candidates(coin):
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

            still_ambiguous_symbols = ambiguous_symbols - set(resolved.keys())
            still_ambiguous_coins = {
                cid: coin
                for cid, (coin, _, _) in selected.items()
                if cid not in matches and coin["symbol"].upper() in still_ambiguous_symbols
            }
            if still_ambiguous_coins:
                social_resolved = _resolve_ambiguous_by_social_match(still_ambiguous_coins, symbol_map)
                for cid, cg_id in social_resolved.items():
                    matches[cid] = (cg_id, "symbol_by_social_match")

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
