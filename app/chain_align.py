"""Best-effort per-chain alignment of CMC's and CoinGecko's contract/explorer
data for one coin, shared by scripts/compare_coin_detail.py (human-readable
comparison) and app/detail_compare.py (gap detection). Extracted from
compare_coin_detail.py so both have exactly one implementation.
"""

from app.criteria import market_universe as mu

# CoinGecko's `links.blockchain_site[]` is a flat list of explorer URLs with
# no chain label attached, unlike CMC's per-platform `contractExplorerUrl`.
# This maps well-known explorer domains to our internal chain slug so they
# can still be lined up per chain; an unrecognized domain lands in its own
# "unlabeled" row instead of being guessed.
EXPLORER_DOMAIN_TO_CHAIN_HINT = {
    "etherscan.io": "ethereum",
    "ethplorer.io": "ethereum",
    "bscscan.com": "bnb",
    "polygonscan.com": "polygon",
    "snowtrace.io": "avalanche",
    "snowscan.xyz": "avalanche",
    "ftmscan.com": "fantom",
    "optimistic.etherscan.io": "optimism",
    "arbiscan.io": "arbitrum",
    "nearblocks.io": "near-protocol",
    "solscan.io": "solana",
    "solanafm.com": "solana",
    "tronscan.org": "tron",
    "hecoinfo.com": "heco",
    "explorer.energi.network": "energi",
    "basescan.org": "base",
    "cronoscan.com": "cronos",
    "moonscan.io": "moonbeam",
    "moonriver.moonscan.io": "moonriver",
    "gnosisscan.io": "gnosis",
    "zkscan.io": "zksync",
    "explorer.zksync.io": "zksync",
    "lineascan.build": "linea",
    "scrollscan.com": "scroll",
    "cardanoscan.io": "cardano",
    "explorer.vechain.org": "vechain",
    "oklink.com": "okb",
    "tonscan.org": "ton",
    "suiscan.xyz": "sui",
    "explorer.sui.io": "sui",
    "xdcscan.io": "xdc-network",
    "mintscan.io": "osmosis",
}

# Reverse of CHAIN_SLUG_CMC_TO_CG, first-key-wins: that dict has more than
# one internal slug mapping to the same CoinGecko chain (e.g. "optimism" and
# "optimism-ethereum" both -> "optimistic-ethereum", covering variant slugs
# CMC's own listing endpoint has been seen to return). A naive {v: k for...}
# inversion lets whichever entry is defined LAST win, which silently split
# Optimism into two separate chain rows (verified live against Synthetix's
# real 9-chain contract list). first-wins makes the choice deterministic and
# picks the canonical, shorter alias.
CG_SLUG_TO_INTERNAL: dict[str, str] = {}
for _internal_slug, _cg_slug in mu.CHAIN_SLUG_CMC_TO_CG.items():
    CG_SLUG_TO_INTERNAL.setdefault(_cg_slug, _internal_slug)


def _prettify_chain(slug: str) -> str:
    return slug.replace("-", " ").replace(":", " ").title()


def align_chains(raw_cmc: dict, raw_cg: dict) -> dict[str, dict]:
    """{chain_key: {label, cmc_contract?, cg_contract?, cmc_explorer?,
    cg_explorer_urls?}} for every chain either side has a contract or
    explorer for. `raw_cmc` is CMC's public per-coin detail response
    (mu.fetch_cmc_detail_raw); `raw_cg` is CoinGecko's `/coins/{id}`
    response (mu.fetch_cg_detail_raw)."""
    chains: dict[str, dict] = {}

    for p in raw_cmc.get("platforms") or []:
        name = (p.get("contractPlatform") or "").strip()
        key = mu.CMC_DETAIL_PLATFORM_NAME_TO_SLUG.get(name.lower()) or f"cmc-{name.lower()}"
        row = chains.setdefault(key, {"label": name or key})
        if name:
            row["label"] = name
        row["cmc_contract"] = p.get("contractAddress")
        row["cmc_explorer"] = p.get("contractExplorerUrl")

    for cg_slug, address in (raw_cg.get("platforms") or {}).items():
        if not cg_slug or not address:
            continue
        key = CG_SLUG_TO_INTERNAL.get(cg_slug, cg_slug)
        row = chains.setdefault(key, {"label": _prettify_chain(key)})
        row["cg_contract"] = address

    for url in (raw_cg.get("links") or {}).get("blockchain_site") or []:
        if not url:
            continue
        domain = mu._normalize_domain(url)
        hint = EXPLORER_DOMAIN_TO_CHAIN_HINT.get(domain or "")
        if hint:
            row = chains.setdefault(hint, {"label": _prettify_chain(hint)})
            row.setdefault("cg_explorer_urls", []).append(url)

    return chains


def unlabeled_cg_explorers(raw_cg: dict) -> list[str]:
    """CoinGecko explorer URLs whose domain didn't match
    EXPLORER_DOMAIN_TO_CHAIN_HINT, so align_chains couldn't place them on a
    chain row."""
    out = []
    for url in (raw_cg.get("links") or {}).get("blockchain_site") or []:
        if not url:
            continue
        domain = mu._normalize_domain(url)
        if not EXPLORER_DOMAIN_TO_CHAIN_HINT.get(domain or ""):
            out.append(url)
    return out
