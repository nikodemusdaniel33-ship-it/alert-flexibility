import requests

from app.config import settings

BASE_URL = "https://api.coingecko.com/api/v3/coins/{id}"


def fetch_socials(coingecko_id: str) -> dict:
    """Fetch a project's listed links from CoinGecko and normalize them.

    Returns a dict of {field: bool} for the fields we track, plus "_raw".
    Raises requests.HTTPError / requests.RequestException on failure.
    """
    headers = {"Accept": "application/json"}
    if settings.coingecko_api_key:
        headers["x-cg-demo-api-key"] = settings.coingecko_api_key

    resp = requests.get(
        BASE_URL.format(id=coingecko_id),
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "false",
            "community_data": "false",
            "developer_data": "false",
        },
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    links = payload.get("links", {})

    return {
        "website": bool(any(links.get("homepage", []))),
        "twitter": bool(links.get("twitter_screen_name")),
        "telegram": bool(links.get("telegram_channel_identifier")),
        "reddit": bool(links.get("subreddit_url")),
        "whitepaper": bool(links.get("whitepaper")),
        "_raw": links,
    }
