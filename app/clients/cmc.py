from app.config import settings
from app.retry import get_with_retry

BASE_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"

# The Pro API's v2/cryptocurrency/info accepts a comma-separated `id` list
# in one call; keeping chunks well under any documented/undocumented URL or
# response-size limit.
BULK_CHUNK_SIZE = 100


def _entry_to_fields(entry: dict) -> dict:
    urls = entry.get("urls", {})
    return {
        "website": bool(urls.get("website")),
        "twitter": bool(urls.get("twitter")),
        "telegram": bool(urls.get("chat")),
        "reddit": bool(urls.get("reddit")),
        "whitepaper": bool(urls.get("technical_doc")),
        "_raw": urls,
    }


def fetch_socials(cmc_id: str) -> dict:
    """Fetch a project's listed links from CoinMarketCap and normalize them.

    Returns a dict of {field: bool} for the fields we track, plus "_raw".
    Raises requests.HTTPError / requests.RequestException on failure.
    """
    resp = get_with_retry(
        BASE_URL,
        params={"id": cmc_id},
        headers={"X-CMC_PRO_API_KEY": settings.cmc_api_key, "Accept": "application/json"},
        timeout=15,
    )
    payload = resp.json()
    entry = payload.get("data", {}).get(str(cmc_id), {})
    return _entry_to_fields(entry)


def fetch_socials_bulk(cmc_ids: list[str]) -> dict[str, dict]:
    """Same as fetch_socials, but for many ids in as few calls as possible
    (v2/cryptocurrency/info takes a comma-separated id list). A single id's
    lookup failure doesn't fail the whole chunk -- CMC omits unknown ids
    from `data` rather than erroring, so those simply won't appear in the
    returned dict.
    """
    out: dict[str, dict] = {}
    for i in range(0, len(cmc_ids), BULK_CHUNK_SIZE):
        chunk = cmc_ids[i : i + BULK_CHUNK_SIZE]
        resp = get_with_retry(
            BASE_URL,
            params={"id": ",".join(chunk)},
            headers={"X-CMC_PRO_API_KEY": settings.cmc_api_key, "Accept": "application/json"},
            timeout=30,
        )
        data = resp.json().get("data", {})
        for cmc_id, entry in data.items():
            out[cmc_id] = _entry_to_fields(entry)
    return out
