"""Pull the full CMC + CoinGecko detail for one coin, given only its CMC id,
and print/export the side-by-side field comparison table -- a general-purpose
version of the by-hand Synthetix comparison this was built from.

The CMC id is the key: it's looked up on CMC's public detail API, mapped to
a CoinGecko id via MarketUniverseProvider's 5-tier matcher (contract address
-> unique symbol -> market-cap dominance -> social-link match; see
app/criteria/market_universe.py), then both sides' full detail is fetched
and merged into one table. No API key needed anywhere in this path.

Usage:
    python -m scripts.compare_coin_detail --cmc-id 2586                 # Synthetix
    python -m scripts.compare_coin_detail --cmc-id 1                    # Bitcoin
    python -m scripts.compare_coin_detail --cmc-id 2586 --cg-id havven  # skip auto-mapping
    python -m scripts.compare_coin_detail --cmc-id 2586 --csv out.csv
    python -m scripts.compare_coin_detail --cmc-id 2586 --xlsx out.xlsx

Chain alignment (contracts/explorers) and tag<->category matching are both
best-effort -- see MATCHED CHAIN / MATCHED TAG comments below for how each
works and where it can fall short. Rows that can't be aligned are kept
separate (one side blank) rather than guessed.
"""

import argparse
import csv
import difflib
import re
import sys

from app.chain_align import align_chains, unlabeled_cg_explorers
from app.config import settings
from app.criteria import market_universe as mu

# High on purpose: a low threshold reliably produces wrong pairs, since any
# two "X Ecosystem" strings share enough characters to score moderately even
# when X differs completely (verified live: at 0.5, "HECO Ecosystem" paired
# with "Base Ecosystem" and "Solana Ecosystem" with "Polygon Ecosystem" --
# both wrong). 0.75 only pairs near-identical text; true matches this misses
# (e.g. CMC's "heco-ecosystem" vs CG's "Huobi ECO Chain Ecosystem", the same
# chain under different names) are left as separate CMC-only/CG-only rows
# rather than guessed.
TAG_MATCH_THRESHOLD = 0.75

# A long shared generic word ("ecosystem", "portfolio"...) can still push
# the full-string ratio above TAG_MATCH_THRESHOLD even when the distinctive
# word is completely different -- verified live: "Binance Ecosystem" vs
# "Base Ecosystem" scores 0.839 on the full string despite "binance"/"base"
# being unrelated. This second gate re-scores after stripping those generic
# words and requires the *distinctive* part to also look alike (0.545 for
# binance/base fails this; 0.364 for heco/harmony fails harder), while a
# real near-match like "synthetics"/"synthetic" (0.947) sails through.
TAG_CORE_MATCH_THRESHOLD = 0.6
TAG_STOPWORDS = {"ecosystem", "portfolio", "index", "token", "tokens", "protocol", "chain", "network"}


def _strip_tag_stopwords(s: str) -> str:
    words = [w for w in s.split() if w not in TAG_STOPWORDS]
    return " ".join(words) or s


def _g(d: dict | None, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def _first(lst) -> str | None:
    """First item of a possibly-None-or-empty list -- CMC's `urls.*` and
    CoinGecko's `links.*` fields are both empty lists (not missing/None)
    when a coin has nothing there, e.g. Bitcoin's `urls.twitter` is `[]`,
    which `[][0]` would crash on."""
    return lst[0] if lst else None


def _fmt(value) -> str:
    if value is None or value == "":
        return "(kosong)"
    if isinstance(value, float):
        return f"{value:,.6g}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v) or "(kosong)"
    return str(value)


def _resolve_coingecko_id(cmc_id: int, raw_cmc: dict, overrides_path: str) -> tuple[str | None, str]:
    """Runs the same 5-tier matcher MarketUniverseProvider uses, against a
    single coin, without needing the ~800-coin batch context it normally
    runs inside."""
    coin = {
        "symbol": raw_cmc["symbol"],
        "platforms": mu._normalize_detail_platforms(raw_cmc.get("platforms")),
    }
    overrides = mu._load_overrides(overrides_path)

    print("  fetching CoinGecko's full coin index (one large call)...", file=sys.stderr)
    contract_map, symbol_map = mu._fetch_coingecko_index()

    cg_id, method = mu._match_coingecko_id(coin, overrides, contract_map, symbol_map)
    if cg_id is not None:
        return cg_id, method
    if method != "ambiguous_symbol":
        return None, method

    symbol = coin["symbol"].upper()
    resolved = mu._resolve_ambiguous_by_market_cap({symbol}, symbol_map)
    if symbol in resolved:
        return resolved[symbol], "symbol_by_market_cap"

    candidates = symbol_map.get(symbol, [])
    urls = raw_cmc.get("urls") or {}
    cmc_domain = mu._normalize_domain((urls.get("website") or [None])[0])
    cmc_twitter = mu._normalize_twitter_handle((urls.get("twitter") or [None])[0])
    if candidates and (cmc_domain or cmc_twitter):
        cg_socials = mu._fetch_cg_social_links(candidates)
        matches = [
            cid
            for cid in candidates
            if cid in cg_socials
            and (
                (cmc_domain and cg_socials[cid]["website_domain"] == cmc_domain)
                or (cmc_twitter and cg_socials[cid]["twitter"] == cmc_twitter)
            )
        ]
        if len(matches) == 1:
            return matches[0], "symbol_by_social_match"

    return None, "ambiguous_symbol"


def _normalize_tag_text(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " ")).strip()


def _match_tags(
    cmc_tags: list[tuple[str, str]], cg_categories: list[str]
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]], list[str]]:
    """Best-effort fuzzy pairing of CMC tags to CoinGecko categories (the two
    platforms use different vocabularies with no shared id) via normalized
    string similarity. Global best-score-first assignment (not per-CMC-tag
    greedy): scoring every pair up front and accepting highest-confidence
    pairs first avoids a weak match locking out a stronger one that would
    otherwise be evaluated later. Returns (matched[(slug,name,cg_category)],
    cmc_only[(slug,name)], cg_only[str])."""
    pairs = []
    for i, (_, name) in enumerate(cmc_tags):
        norm_name = _normalize_tag_text(name)
        core_name = _strip_tag_stopwords(norm_name)
        for j, cg_cat in enumerate(cg_categories):
            norm_cat = _normalize_tag_text(cg_cat)
            score = difflib.SequenceMatcher(None, norm_name, norm_cat).ratio()
            if score < TAG_MATCH_THRESHOLD:
                continue
            core_score = difflib.SequenceMatcher(None, core_name, _strip_tag_stopwords(norm_cat)).ratio()
            if core_score < TAG_CORE_MATCH_THRESHOLD:
                continue
            pairs.append((score, i, j))
    pairs.sort(reverse=True)

    used_cmc: set[int] = set()
    used_cg: set[int] = set()
    matched = []
    for _, i, j in pairs:
        if i in used_cmc or j in used_cg:
            continue
        slug, name = cmc_tags[i]
        matched.append((slug, name, cg_categories[j]))
        used_cmc.add(i)
        used_cg.add(j)

    unmatched_cmc = [cmc_tags[i] for i in range(len(cmc_tags)) if i not in used_cmc]
    cg_only = [cg_categories[j] for j in range(len(cg_categories)) if j not in used_cg]
    return matched, unmatched_cmc, cg_only


def build_rows(raw_cmc: dict, raw_cg: dict) -> list[tuple]:
    """Returns a flat list of rows: ("section", label) for a section header,
    or (info, field_cmc, field_cg, data_cmc, data_cg) for a data row --
    same shape the Synthetix comparison table used, just built dynamically
    from whatever the two APIs actually returned for this coin."""
    stats = raw_cmc.get("statistics") or {}
    md = raw_cg.get("market_data") or {}
    rows: list[tuple] = []

    rows.append(("section", "Identitas"))
    rows.append(("Platform ID", "id", "id", raw_cmc.get("id"), raw_cg.get("id")))
    rows.append(("Symbol", "symbol", "symbol", raw_cmc.get("symbol"), raw_cg.get("symbol")))
    rows.append(("Name", "name", "name", raw_cmc.get("name"), raw_cg.get("name")))
    rows.append(("Slug", "slug", "id", raw_cmc.get("slug"), raw_cg.get("id")))
    rank_cmc = stats.get("rank")
    rows.append(
        ("Rank", "statistics.rank", "market_cap_rank",
         f"#{rank_cmc}" if rank_cmc else None, f"#{raw_cg.get('market_cap_rank')}" if raw_cg.get("market_cap_rank") else None)
    )

    rows.append(("section", "Market Data"))
    rows.append(("Current Price", "statistics.price", "market_data.current_price.usd", stats.get("price"), _g(md, "current_price", "usd")))
    rows.append(("Price Change 24h", "statistics.priceChangePercentage24h", "market_data.price_change_percentage_24h", stats.get("priceChangePercentage24h"), md.get("price_change_percentage_24h")))
    rows.append(("Market Cap", "statistics.marketCap", "market_data.market_cap.usd", stats.get("marketCap"), _g(md, "market_cap", "usd")))
    rows.append(("Fully Diluted Valuation", "statistics.fullyDilutedMarketCap", "market_data.fully_diluted_valuation.usd", stats.get("fullyDilutedMarketCap"), _g(md, "fully_diluted_valuation", "usd")))
    rows.append(("Volume 24h", "statistics.volume24h", "market_data.total_volume.usd", stats.get("volume24h"), _g(md, "total_volume", "usd")))
    rows.append(("Volume 7d", "statistics.volume7d", None, stats.get("volume7d"), None))
    rows.append(("Volume 30d", "statistics.volume30d", None, stats.get("volume30d"), None))
    rows.append(("TVL (USD)", None, "market_data.total_value_locked.usd", None, _g(md, "total_value_locked", "usd")))
    rows.append(("TVL (BTC)", None, "market_data.total_value_locked.btc", None, _g(md, "total_value_locked", "btc")))
    rows.append(("ATH", "statistics.highAllTime", "market_data.ath.usd", stats.get("highAllTime"), _g(md, "ath", "usd")))
    rows.append(("ATL", "statistics.lowAllTime", "market_data.atl.usd", stats.get("lowAllTime"), _g(md, "atl", "usd")))

    rows.append(("section", "Supply"))
    rows.append(("Circulating Supply", "statistics.circulatingSupply", "market_data.circulating_supply", stats.get("circulatingSupply"), md.get("circulating_supply")))
    rows.append(("Self-Reported Circ. Supply", "selfReportedCirculatingSupply", None, raw_cmc.get("selfReportedCirculatingSupply"), None))
    rows.append(("Total Supply", "statistics.totalSupply", "market_data.total_supply", stats.get("totalSupply"), md.get("total_supply")))
    rows.append(("Max Supply", "statistics.maxSupply", "market_data.max_supply", stats.get("maxSupply"), md.get("max_supply") if md.get("max_supply") else "∞ (null)"))

    # --- Contracts & explorers per chain (best-effort alignment) ---
    chains = align_chains(raw_cmc, raw_cg)
    other_cg_explorers = unlabeled_cg_explorers(raw_cg)
    ordered_keys = sorted(chains.keys(), key=lambda k: chains[k]["label"].lower())

    rows.append(("section", "Contract per Chain"))
    for key in ordered_keys:
        c = chains[key]
        if not c.get("cmc_contract") and not c.get("cg_contract"):
            continue  # chain only known from an explorer-domain hint, no actual contract on either side
        rows.append((f"Contract - {c['label']}", "platform(s).token_address", "platforms.<chain>", c.get("cmc_contract"), c.get("cg_contract")))

    rows.append(("section", "Block Explorer per Chain"))
    for key in ordered_keys:
        c = chains[key]
        cg_explorers = c.get("cg_explorer_urls")
        if not c.get("cmc_explorer") and not cg_explorers:
            continue
        rows.append(
            (f"Explorer - {c['label']}", "platforms[].contractExplorerUrl", "links.blockchain_site[]",
             c.get("cmc_explorer"), cg_explorers)
        )
    if other_cg_explorers:
        rows.append(("Explorer - Other (CoinGecko, unrecognized domain)", None, "links.blockchain_site[]", None, other_cg_explorers))

    # --- Socials ---
    urls = raw_cmc.get("urls") or {}
    links = raw_cg.get("links") or {}
    rows.append(("section", "Sosial & Link"))
    rows.append(("Website", "urls.website[0]", "links.homepage[0]", _first(urls.get("website")), _first(links.get("homepage"))))
    rows.append(("Twitter/X", "urls.twitter[0]", "links.twitter_screen_name", _first(urls.get("twitter")), links.get("twitter_screen_name")))
    rows.append(("Reddit", "urls.reddit[0]", "links.subreddit_url", _first(urls.get("reddit")), links.get("subreddit_url")))
    rows.append(("Discord/Chat", "urls.chat[0]", "links.chat_url[0]", _first(urls.get("chat")), _first(links.get("chat_url"))))
    rows.append(("Technical Doc/Whitepaper", "urls.technical_doc[0]", "links.whitepaper", _first(urls.get("technical_doc")), links.get("whitepaper")))
    rows.append(("Source Code (Github)", "urls.source_code[0]", "links.repos_url.github[]", urls.get("source_code"), _g(links, "repos_url", "github")))
    rows.append(("Message Board/Forum", "urls.message_board[0]", "links.official_forum_url", _first(urls.get("message_board")), links.get("official_forum_url")))
    rows.append(("Announcement/Blog", "urls.announcement[0]", "links.announcement_url[0]", _first(urls.get("announcement")), _first(links.get("announcement_url"))))
    rows.append(("Facebook", "urls.facebook", "links.facebook_username", urls.get("facebook"), links.get("facebook_username")))

    # --- CMC-only extras ---
    holders = raw_cmc.get("holders") or {}
    ratings = raw_cmc.get("cryptoRating") or []
    certik = next((r for r in ratings if r.get("type") == "CertiK"), ratings[0] if ratings else {})
    audits = raw_cmc.get("auditInfos") or raw_cmc.get("auditInfoList") or []
    audit = audits[0] if audits else {}
    unlock = raw_cmc.get("tokenUnlockLatest") or {}
    pools = _g(stats, "topLiquidityPools", default=[]) or []
    top_pool = pools[0] if pools else {}
    top_pool_str = None
    if top_pool:
        liq = top_pool.get("liquidityUsd")
        top_pool_str = f"{top_pool.get('pairName')} @ {top_pool.get('exchangeName')}" + (f", ${liq:,.0f}" if liq else "")

    rows.append(("section", "Metadata Tambahan (CMC-only)"))
    rows.append(("Date Added", "dateAdded", None, raw_cmc.get("dateAdded"), None))
    rows.append(("Holder Count", "holders.holderCount", None, holders.get("holderCount"), None))
    rows.append(("Top 10 Holder Ratio", "holders.topTenHolderRatio", None, holders.get("topTenHolderRatio"), None))
    rows.append(("Top 20 Holder Ratio", "holders.topTwentyHolderRatio", None, holders.get("topTwentyHolderRatio"), None))
    rows.append(("Top 50 Holder Ratio", "holders.topFiftyHolderRatio", None, holders.get("topFiftyHolderRatio"), None))
    rows.append(("Top 100 Holder Ratio", "holders.topHundredHolderRatio", None, holders.get("topHundredHolderRatio"), None))
    rows.append(("Rating Score", "cryptoRating[].score", None, f"{certik['score']} ({certik.get('type', '?')})" if certik.get("score") else None, None))
    rows.append(("Audit", "auditInfos[0].auditor", None, audit.get("auditor"), None))
    rows.append(("Token Unlock %", "tokenUnlockLatest.totalUnlockedPercentage", None, unlock.get("totalUnlockedPercentage"), None))
    rows.append(("Top Liquidity Pool", "statistics.topLiquidityPools[0]", None, top_pool_str, None))

    # --- Tags / categories (best-effort fuzzy match) ---
    cmc_tags = [(t.get("slug", ""), t.get("name", "")) for t in (raw_cmc.get("tags") or []) if t.get("name")]
    cg_categories = [c for c in (raw_cg.get("categories") or []) if c]
    matched, cmc_only, cg_only = _match_tags(cmc_tags, cg_categories)

    if matched:
        rows.append(("section", "Tags/Categories - Match (fuzzy, best-effort)"))
        for slug, name, cg_cat in matched:
            rows.append((f"Tag: {name}", slug, "categories[]", name, cg_cat))
    if cmc_only:
        rows.append(("section", "Tags - CMC-only"))
        for slug, name in cmc_only:
            rows.append((f"Tag: {name}", slug, None, name, None))
    if cg_only:
        rows.append(("section", "Categories - CG-only"))
        for cg_cat in cg_only:
            rows.append((f"Cat: {cg_cat}", None, "categories[]", None, cg_cat))

    return rows


def print_table(rows: list[tuple], name: str, symbol: str) -> None:
    print(f"\n=== {name} ({symbol}) — CMC vs CoinGecko field comparison ===\n")
    for row in rows:
        if row[0] == "section":
            print(f"\n-- {row[1]} --")
            continue
        info, field_cmc, field_cg, data_cmc, data_cg = row
        print(f"  {info}")
        print(f"      CMC: {field_cmc or '-':<45} {_fmt(data_cmc)}")
        print(f"      CG : {field_cg or '-':<45} {_fmt(data_cg)}")


def write_csv(rows: list[tuple], path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["section", "informasi", "field_cmc", "field_cg", "data_cmc", "data_cg"])
        section = ""
        for row in rows:
            if row[0] == "section":
                section = row[1]
                continue
            info, field_cmc, field_cg, data_cmc, data_cg = row
            w.writerow([section, info, field_cmc or "", field_cg or "", _fmt(data_cmc), _fmt(data_cg)])
    print(f"wrote {path}", file=sys.stderr)


def write_xlsx(rows: list[tuple], path: str, title: str) -> None:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:31]

    font_name = "Arial"
    header_font = Font(name=font_name, bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    section_font = Font(name=font_name, bold=True, color="1F4E78", size=11)
    section_fill = PatternFill("solid", fgColor="D9E1F2")
    normal_font = Font(name=font_name, size=10)
    empty_font = Font(name=font_name, size=10, italic=True, color="999999")
    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.merge_cells("A1:E1")
    ws["A1"] = title
    ws["A1"].font = Font(name=font_name, bold=True, size=14, color="1F4E78")
    ws.row_dimensions[1].height = 26

    header_row = 3
    for col, h in enumerate(["Informasi", "Field CMC", "Field CG", "Data CMC", "Data CG"], start=1):
        c = ws.cell(row=header_row, column=col, value=h)
        c.font, c.fill, c.border = header_font, header_fill, border
        c.alignment = Alignment(horizontal="left", vertical="center")

    r = header_row + 1
    for row in rows:
        if row[0] == "section":
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
            c = ws.cell(row=r, column=1, value=row[1])
            c.font, c.fill = section_font, section_fill
            for col in range(1, 6):
                ws.cell(row=r, column=col).fill = section_fill
                ws.cell(row=r, column=col).border = border
            r += 1
            continue
        info, field_cmc, field_cg, data_cmc, data_cg = row
        values = [info, field_cmc, field_cg, _fmt(data_cmc), _fmt(data_cg)]
        for col, val in enumerate(values, start=1):
            c = ws.cell(row=r, column=col, value=val)
            c.border = border
            c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            c.font = empty_font if (col >= 4 and val == "(kosong)") else normal_font
        r += 1

    for col, w in zip("ABCDE", (30, 30, 30, 42, 42)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = f"A{header_row + 1}"
    ws.sheet_view.showGridLines = False

    wb.save(path)
    print(f"wrote {path}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cmc-id", type=int, required=True, help="CMC cryptocurrency id (the key).")
    parser.add_argument("--cg-id", type=str, default=None, help="Skip auto-mapping and use this CoinGecko id directly.")
    parser.add_argument("--overrides", type=str, default=None, help="Override yaml path (default: MARKET_UNIVERSE_OVERRIDES_PATH setting).")
    parser.add_argument("--csv", type=str, default=None, help="Write the comparison table to this CSV path.")
    parser.add_argument("--xlsx", type=str, default=None, help="Write a styled comparison workbook to this path (needs openpyxl).")
    args = parser.parse_args()

    print(f"Fetching CMC detail for id={args.cmc_id}...", file=sys.stderr)
    raw_cmc = mu.fetch_cmc_detail_raw(args.cmc_id)

    if args.cg_id:
        cg_id, method = args.cg_id, "manual_override_flag"
    else:
        print("Mapping to CoinGecko id (5-tier matcher)...", file=sys.stderr)
        cg_id, method = _resolve_coingecko_id(
            args.cmc_id, raw_cmc, args.overrides or settings.market_universe_overrides_path
        )

    if not cg_id:
        print(
            f"Could not map CMC id {args.cmc_id} ({raw_cmc.get('symbol')}) to a CoinGecko id "
            f"(reason: {method}). Pass --cg-id to supply it manually, or add an entry to "
            f"{args.overrides or settings.market_universe_overrides_path}.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"  -> matched to CoinGecko id '{cg_id}' (method: {method})", file=sys.stderr)
    print(f"Fetching CoinGecko detail for id={cg_id}...", file=sys.stderr)
    raw_cg = mu.fetch_cg_detail_raw(cg_id, market_data=True, community_data=True)

    rows = build_rows(raw_cmc, raw_cg)
    print_table(rows, raw_cmc.get("name", "?"), raw_cmc.get("symbol", "?"))

    if args.csv:
        write_csv(rows, args.csv)
    if args.xlsx:
        title = f"{raw_cmc.get('name', 'Coin')} ({raw_cmc.get('symbol', '')}) - CMC vs CG"
        write_xlsx(rows, args.xlsx, title)


if __name__ == "__main__":
    main()
