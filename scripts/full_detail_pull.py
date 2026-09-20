"""Pull raw CMC and CoinGecko field values for coins in cmc_universe's
current snapshot into two separate tables -- cmc_field_details and
cg_field_details (`python -m scripts.full_detail_pull [--limit N]
[--cmc-id ID]`).

CMC detail is pulled for EVERY coin in cmc_universe, whether or not it
has a resolved CoinGecko id -- CMC's own data doesn't need one.
CoinGecko detail is only pulled for coins with a resolved (valid=True)
cmc_cg_mapping row, since that side needs a cg_id to know what to fetch.
A coin without a valid mapping (or whose CoinGecko fetch fails this run)
ends up with cmc_field_details rows and no cg_field_details rows --
scripts.build_field_contrast already only processes coins with a valid
mapping, so this is a normal, unremarkable state, not a partial failure
to fix.

Long/normalized, one table per source: each row is (id, field_type,
field_name, value) -- e.g. (1975, social, twitter) in cmc_field_details or
(chainlink, social, twitter) in cg_field_details. Every row also carries
project_name/project_url (that coin's name and its catalog page on this
row's own source), denormalized so either table is browsable on its own
without a join. Comparing the two (which field differs for a given coin)
is left to a join via cmc_cg_mapping at query time rather than
precomputed and stored -- see app.detail_compare for the same
field-by-field comparison used by the live alerting worker, which still
computes and stores diffs directly against tracked Projects.

Uses the same key-free raw-detail fetches as the live worker
(app.criteria.market_universe.fetch_cmc_detail_raw/fetch_cg_detail_raw).
Neither side has a bulk detail endpoint for this -- CMC's public detail
API and CoinGecko's /coins/{id} are both one-request-per-coin, paced by
retry-with-backoff on rate limits. CoinGecko's free tier is the slow part
in practice (see README for COINGECKO_API_KEY, which raises the limit
substantially). Use --cmc-id to pull a single coin while testing, or
--limit to cap a run to the first N coins in the universe.

Snapshot, not incremental: every run replaces each target coin's rows
outright (delete then insert), whether or not that coin already had rows
from a previous run.

A fetch failure -- CMC or CoinGecko -- is recorded in detail_pull_failures
(source, cmc_id, reason) instead of only logged, since this job runs long
enough that its own run logs have proven unreliable to rely on after the
fact (see README). That table tracks CURRENTLY outstanding failures only:
a coin's row there is cleared the moment its fetch succeeds again.
"""

import argparse
import logging
import time
from datetime import datetime, timezone

import requests

from app.criteria import market_universe as mu
from app.criteria.market_universe import (
    CMC_DETAIL_REQUEST_DELAY_SECONDS,
    fetch_cg_detail_raw,
    fetch_cmc_detail_raw,
)
from app.chain_align import CG_SLUG_TO_INTERNAL, EXPLORER_DOMAIN_TO_CHAIN_HINT
from app.db import SessionLocal, ensure_schema
from app.detail_compare import _cg_links, _cmc_urls, _first
from app.market_data.models import CgFieldDetail, CmcCgMapping, CmcFieldDetail, CmcUniverse, DetailPullFailure

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("full_detail_pull")

PROGRESS_EVERY = 50


def _universe(db) -> set[str]:
    """CMC ids currently in cmc_universe (itself the union of cmc_top600's
    and cmc_binance_listed's latest batches -- see
    scripts.build_cmc_universe). cmc_universe holds a single current
    snapshot (replace semantics), so this is just every row in it."""
    return {r.cmc_id for r in db.query(CmcUniverse.cmc_id)}


def _as_text(value) -> str | None:
    """A field value is usually a plain string, but github's is
    list-valued (a coin can have several repos) -- render that as a
    comma-joined string instead of Python's list repr. Only None/empty
    counts as absent -- a legitimate 0 (e.g. 0% price change) must not
    collapse to None the way a bare `not value` check would."""
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v) or None
    if isinstance(value, str) and not value:
        return None
    return str(value)


def _cmc_social_values(raw_cmc: dict) -> dict[str, object]:
    # cmc_url has no CoinGecko counterpart (there's nothing to compare it
    # against -- it's CMC's own catalog page for the coin) and is
    # deliberately left out of build_field_contrast.SOCIAL_FIELDS, so it
    # never reaches coin_field_contrast/gap_details. It's here purely as a
    # convenience reference field on cmc_field_details itself -- distinct
    # from the project_url column (see _cmc_rows), which denormalizes the
    # same URL onto every row rather than just the social ones.
    urls = _cmc_urls(raw_cmc)
    return {
        "website": _first(urls.get("website")),
        "twitter": _first(urls.get("twitter")),
        "reddit": _first(urls.get("reddit")),
        "discord": _first(urls.get("chat")),
        "whitepaper": _first(urls.get("technical_doc")),
        "forum": _first(urls.get("message_board")),
        "blog": _first(urls.get("announcement")),
        "facebook": _first(urls.get("facebook")),
        "github": urls.get("source_code"),
        "cmc_url": mu.cmc_currency_url(raw_cmc.get("slug")),
    }


def _cg_social_values(raw_cg: dict) -> dict[str, object]:
    links = _cg_links(raw_cg)
    return {
        "website": _first(links.get("homepage")),
        "twitter": links.get("twitter_screen_name"),
        "reddit": links.get("subreddit_url"),
        "discord": _first(links.get("chat_url")),
        "whitepaper": links.get("whitepaper"),
        "forum": _first(links.get("official_forum_url")),
        "blog": _first(links.get("announcement_url")),
        "facebook": links.get("facebook_username"),
        "github": (links.get("repos_url") or {}).get("github"),
    }


# CMC's public detail API has no self-reported-circulating-supply field
# (only a self-reported *market cap*, which is a different number) and
# CoinGecko has no such concept at all -- this stays null on both sides
# until/unless a source for it turns up.
_SELF_REPORTED_CIRC_SUPPLY_UNAVAILABLE = None


def _cmc_market_values(raw_cmc: dict) -> dict[str, object]:
    stats = raw_cmc.get("statistics") or {}
    return {
        "current_price": stats.get("price"),
        "price_change_24h": stats.get("priceChangePercentage24h"),
        "market_cap": stats.get("marketCap"),
        "volume_24h": raw_cmc.get("volume"),
        "circulating_supply": stats.get("circulatingSupply"),
        "self_reported_circulating_supply": _SELF_REPORTED_CIRC_SUPPLY_UNAVAILABLE,
        "total_supply": stats.get("totalSupply"),
        "max_supply": stats.get("maxSupply"),
    }


def _cg_market_values(raw_cg: dict) -> dict[str, object]:
    md = raw_cg.get("market_data") or {}

    def usd(key):
        v = md.get(key)
        return v.get("usd") if isinstance(v, dict) else v

    return {
        "current_price": usd("current_price"),
        "price_change_24h": usd("price_change_percentage_24h"),
        "market_cap": usd("market_cap"),
        "volume_24h": usd("total_volume"),
        "circulating_supply": md.get("circulating_supply"),
        "self_reported_circulating_supply": _SELF_REPORTED_CIRC_SUPPLY_UNAVAILABLE,
        "total_supply": md.get("total_supply"),
        "max_supply": md.get("max_supply"),
    }


def _cmc_rows(cmc_id: str, raw_cmc: dict, pulled_at) -> list[CmcFieldDetail]:
    project_name = raw_cmc.get("name")
    project_url = mu.cmc_currency_url(raw_cmc.get("slug"))

    def row(field_type: str, field_name: str, value) -> CmcFieldDetail:
        return CmcFieldDetail(
            cmc_id=cmc_id,
            field_type=field_type,
            field_name=field_name,
            value=_as_text(value),
            project_name=project_name,
            project_url=project_url,
            pulled_at=pulled_at,
        )

    rows = [row("social", name, value) for name, value in _cmc_social_values(raw_cmc).items()]
    rows.extend(row("market_data", name, value) for name, value in _cmc_market_values(raw_cmc).items())
    for t in raw_cmc.get("tags") or []:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name:
            continue
        slug = t.get("slug") or name
        rows.append(row("tags", slug, name))

    for p in raw_cmc.get("platforms") or []:
        name = (p.get("contractPlatform") or "").strip()
        slug = mu.CMC_DETAIL_PLATFORM_NAME_TO_SLUG.get(name.lower()) or f"cmc-{name.lower()}"
        if p.get("contractAddress"):
            rows.append(row("contract", slug, p["contractAddress"]))
        if p.get("contractExplorerUrl"):
            rows.append(row("explorer", slug, p["contractExplorerUrl"]))
    return rows


def _cg_rows(cg_id: str, raw_cg: dict, pulled_at) -> list[CgFieldDetail]:
    project_name = raw_cg.get("name")
    project_url = mu.cg_currency_url(cg_id)

    def row(field_type: str, field_name: str, value) -> CgFieldDetail:
        return CgFieldDetail(
            cg_id=cg_id,
            field_type=field_type,
            field_name=field_name,
            value=_as_text(value),
            project_name=project_name,
            project_url=project_url,
            pulled_at=pulled_at,
        )

    rows = [row("social", name, value) for name, value in _cg_social_values(raw_cg).items()]
    rows.extend(row("market_data", name, value) for name, value in _cg_market_values(raw_cg).items())
    for category in raw_cg.get("categories") or []:
        if category:
            rows.append(row("tags", category, category))

    for cg_slug, address in (raw_cg.get("platforms") or {}).items():
        if not cg_slug or not address:
            continue
        slug = CG_SLUG_TO_INTERNAL.get(cg_slug, cg_slug)
        rows.append(row("contract", slug, address))

    explorers_by_chain: dict[str, list[str]] = {}
    for url in (raw_cg.get("links") or {}).get("blockchain_site") or []:
        if not url:
            continue
        domain = mu._normalize_domain(url)
        hint = EXPLORER_DOMAIN_TO_CHAIN_HINT.get(domain or "")
        if hint:
            explorers_by_chain.setdefault(hint, []).append(url)
    for slug, urls in explorers_by_chain.items():
        rows.append(row("explorer", slug, ", ".join(urls)))

    return rows


def _record_failure(db, source: str, cmc_id: str, cg_id: str | None, reason: str) -> None:
    db.query(DetailPullFailure).filter(DetailPullFailure.source == source, DetailPullFailure.cmc_id == cmc_id).delete(synchronize_session=False)
    db.add(DetailPullFailure(source=source, cmc_id=cmc_id, cg_id=cg_id, reason=reason, failed_at=datetime.now(timezone.utc)))


def _clear_failure(db, source: str, cmc_id: str) -> None:
    db.query(DetailPullFailure).filter(DetailPullFailure.source == source, DetailPullFailure.cmc_id == cmc_id).delete(synchronize_session=False)


def run(limit: int | None = None, cmc_id: str | None = None) -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        if cmc_id:
            cmc_ids = [cmc_id]
        else:
            cmc_ids = sorted(_universe(db))
            if not cmc_ids:
                log.warning("cmc_universe is empty -- run pull_top600, pull_binance_listed, and build_cmc_universe first.")
                return
            if limit:
                cmc_ids = cmc_ids[:limit]

        mappings = {m.cmc_id: m for m in db.query(CmcCgMapping).filter(CmcCgMapping.cmc_id.in_(cmc_ids)).all()}

        targets = cmc_ids
        with_cg = sum(1 for cid in targets if mappings.get(cid) and mappings[cid].valid and mappings[cid].cg_id)
        log.info(
            "Pulling CMC detail for %d coins (%d of those also get CoinGecko detail, valid mapping); "
            "replaces each coin's existing rows",
            len(targets),
            with_cg,
        )

        for i, cid in enumerate(targets, start=1):
            m = mappings.get(cid)
            cg_id = m.cg_id if (m and m.valid and m.cg_id) else None

            try:
                raw_cmc = fetch_cmc_detail_raw(int(cid))
            except (requests.RequestException, ValueError, KeyError) as exc:
                log.warning("skipping cmc_id=%s: CMC fetch failed (%s)", cid, exc)
                _record_failure(db, "cmc", cid, None, str(exc))
                if i < len(targets):
                    time.sleep(CMC_DETAIL_REQUEST_DELAY_SECONDS)
                continue

            _clear_failure(db, "cmc", cid)
            pulled_at = datetime.now(timezone.utc)
            db.query(CmcFieldDetail).filter(CmcFieldDetail.cmc_id == cid).delete(synchronize_session=False)
            db.add_all(_cmc_rows(cid, raw_cmc, pulled_at))

            if cg_id:
                try:
                    raw_cg = fetch_cg_detail_raw(cg_id, market_data=True, community_data=True)
                except requests.RequestException as exc:
                    log.warning("cmc_id=%s: CoinGecko fetch failed (%s) -- CMC side saved, CG side left as-is", cid, exc)
                    _record_failure(db, "cg", cid, cg_id, str(exc))
                else:
                    _clear_failure(db, "cg", cid)
                    db.query(CgFieldDetail).filter(CgFieldDetail.cg_id == cg_id).delete(synchronize_session=False)
                    db.add_all(_cg_rows(cg_id, raw_cg, pulled_at))

            if i < len(targets):
                time.sleep(CMC_DETAIL_REQUEST_DELAY_SECONDS)

            if i % PROGRESS_EVERY == 0:
                db.commit()
                log.info("  %d/%d coins pulled", i, len(targets))

        db.commit()
        log.info("Done: cmc_field_details/cg_field_details updated for %d coins", len(targets))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Only pull the first N coins (for testing).")
    parser.add_argument("--cmc-id", type=str, default=None, help="Only pull this one CMC id (for testing).")
    args = parser.parse_args()
    run(args.limit, args.cmc_id)
