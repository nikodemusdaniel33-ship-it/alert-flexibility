"""Detects data-completeness gaps between CMC's and CoinGecko's full
per-coin detail, per the field list and monitoring rules from the reviewed
Synthetix CMC-vs-CoinGecko spec:

- Social/link fields (9): gap when the two sides' values are DIFFERENT
  (not just when CMC's is empty) -- e.g. CMC missing a Discord CG has, but
  also CMC pointing at a stale/wrong link CG has since replaced. Values are
  normalized first (domain/handle-level) so equivalent formats (a full URL
  vs a bare handle, `discord.gg/x` vs `discord.com/invite/x`) don't
  register as a false difference.
- Contract / explorer per chain: gap when CoinGecko lists a chain CMC
  doesn't (one gap per missing chain, not one combined gap per coin) --
  uses app/chain_align.py's alignment, shared with scripts/compare_coin_detail.py.
- Identity, market data, supply, and tags/categories are informational only
  (shown on the detail page) and never produce a gap -- per the same spec.

`raw_cmc` throughout is CMC's public per-coin detail response
(app.criteria.market_universe.fetch_cmc_detail_raw); `raw_cg` is
CoinGecko's `/coins/{id}` response with market_data+community_data=True
(app.criteria.market_universe.fetch_cg_detail_raw).
"""

import re

from app.chain_align import align_chains
from app.criteria.market_universe import _normalize_domain, _normalize_twitter_handle


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _normalize_handle(value: str | None, *, strip_prefixes: tuple[str, ...] = ()) -> str | None:
    """Lowercase, strip protocol/www, strip any of the given path prefixes
    (e.g. "invite/" for Discord), then take the last non-empty path
    segment. Used for fields that are conceptually "a handle/code",
    regardless of which of several URL shapes a given API returns it as."""
    if not value:
        return None
    v = value.strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    v = v.split("?")[0].rstrip("/")
    parts = [p for p in v.split("/") if p]
    if not parts:
        return None
    # drop the domain (first segment) and any known prefix segments
    parts = parts[1:] if len(parts) > 1 else parts
    parts = [p for p in parts if p not in strip_prefixes]
    return parts[-1] if parts else None


def _normalize_url_path(value: str | None) -> str | None:
    """Lowercase, strip protocol/www/trailing slash -- for fields compared
    as "the same link", not reduced to a bare handle."""
    if not value:
        return None
    v = value.strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    return v.split("?")[0].rstrip("/") or None


def _github_paths(value) -> set[str]:
    """{"owner/repo", ...} extracted from one URL or a list of them --
    CoinGecko's repos_url.github is a list (a project can have several
    repos); CMC's source_code is effectively one. Compared as sets so a
    CMC entry matching ANY of CoinGecko's repos counts as present."""
    urls = value if isinstance(value, list) else ([value] if value else [])
    paths = set()
    for url in urls:
        if not url:
            continue
        normalized = _normalize_url_path(url)
        if not normalized:
            continue
        segments = normalized.split("/")
        # domain + first two path segments (owner/repo); shorter is kept as-is.
        paths.add("/".join(segments[1:3]) if len(segments) > 1 else normalized)
    return paths


def _cmc_urls(raw_cmc: dict) -> dict:
    return raw_cmc.get("urls") or {}


def _cg_links(raw_cg: dict) -> dict:
    return raw_cg.get("links") or {}


# (key, label, cmc_value, cg_value, normalizer) -- normalizer(None) must be
# None so both-empty never registers as a difference.
def _social_field_specs(raw_cmc: dict, raw_cg: dict) -> list[tuple[str, str, object, object, object]]:
    urls = _cmc_urls(raw_cmc)
    links = _cg_links(raw_cg)
    return [
        ("website", "Website", _first(urls.get("website")), _first(links.get("homepage")), _normalize_domain),
        ("twitter", "Twitter/X", _first(urls.get("twitter")), links.get("twitter_screen_name"), _normalize_twitter_handle),
        ("reddit", "Reddit", _first(urls.get("reddit")), links.get("subreddit_url"), _normalize_url_path),
        (
            "discord",
            "Discord/Chat",
            _first(urls.get("chat")),
            _first(links.get("chat_url")),
            lambda v: _normalize_handle(v, strip_prefixes=("invite",)),
        ),
        ("whitepaper", "Technical Doc/Whitepaper", _first(urls.get("technical_doc")), links.get("whitepaper"), _normalize_url_path),
        ("forum", "Message Board/Forum", _first(urls.get("message_board")), _first(links.get("official_forum_url")), _normalize_url_path),
        ("blog", "Announcement/Blog", _first(urls.get("announcement")), _first(links.get("announcement_url")), _normalize_url_path),
        ("facebook", "Facebook", _first(urls.get("facebook")), links.get("facebook_username"), lambda v: _normalize_handle(v)),
    ]


def social_diffs(raw_cmc: dict, raw_cg: dict) -> dict[str, dict]:
    """{key: {label, cmc_value, cg_value, differs}} for the 8 single-value
    social fields, plus "github" handled separately since it's list-valued
    on CoinGecko's side."""
    out = {}
    for key, label, cmc_value, cg_value, normalize in _social_field_specs(raw_cmc, raw_cg):
        out[key] = {
            "label": label,
            "cmc_value": cmc_value,
            "cg_value": cg_value,
            "differs": normalize(cmc_value) != normalize(cg_value),
        }

    cmc_github = _cmc_urls(raw_cmc).get("source_code")
    cg_github = (_cg_links(raw_cg).get("repos_url") or {}).get("github")
    cmc_paths = _github_paths(cmc_github)
    cg_paths = _github_paths(cg_github)
    out["github"] = {
        "label": "Source Code (Github)",
        "cmc_value": cmc_github,
        "cg_value": cg_github,
        "differs": bool(cg_paths) and not (cmc_paths & cg_paths),
    }
    return out


def field_checklist(raw_cmc: dict, raw_cg: dict) -> dict[str, bool]:
    """{field_name: is_missing} for every field this coin should be
    checked on right now: the 9 social fields (always) plus one entry per
    chain either side has a contract or explorer for (dynamic per coin).
    `is_missing` drives Gap open/resolve in app/worker.py -- True for a
    social field means "differs from CoinGecko"; for contract/explorer it
    means "CoinGecko has this chain, CMC doesn't"."""
    checklist: dict[str, bool] = {}

    for key, diff in social_diffs(raw_cmc, raw_cg).items():
        checklist[f"social:{key}"] = diff["differs"]

    for chain_key, chain in align_chains(raw_cmc, raw_cg).items():
        if chain.get("cg_contract"):
            checklist[f"contract:{chain_key}"] = not chain.get("cmc_contract")
        if chain.get("cg_explorer_urls"):
            checklist[f"explorer:{chain_key}"] = not chain.get("cmc_explorer")

    return checklist
