TRACKED_FIELDS = ["website", "twitter", "telegram", "reddit", "whitepaper"]


def missing_in_cmc(cmc_fields: dict, cg_fields: dict) -> list[str]:
    """Fields CoinGecko has listed that CoinMarketCap is missing."""
    return [
        field
        for field in TRACKED_FIELDS
        if cg_fields.get(field) and not cmc_fields.get(field)
    ]
