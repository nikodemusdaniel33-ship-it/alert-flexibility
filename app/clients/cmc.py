import requests

from app.config import settings

BASE_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"


def fetch_socials(cmc_id: str) -> dict:
    """Fetch a project's listed links from CoinMarketCap and normalize them.

    Returns a dict of {field: bool} for the fields we track, plus "_raw".
    Raises requests.HTTPError / requests.RequestException on failure.
    """
    resp = requests.get(
        BASE_URL,
        params={"id": cmc_id},
        headers={"X-CMC_PRO_API_KEY": settings.cmc_api_key, "Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    entry = payload.get("data", {}).get(str(cmc_id), {})
    urls = entry.get("urls", {})

    return {
        "website": bool(urls.get("website")),
        "twitter": bool(urls.get("twitter")),
        "telegram": bool(urls.get("chat")),
        "reddit": bool(urls.get("reddit")),
        "whitepaper": bool(urls.get("technical_doc")),
        "_raw": urls,
    }
