"""Shared HTTP retry helper for the CMC/CoinGecko public and Pro APIs, all
of which rate-limit routinely (not exceptionally) on free/lower tiers."""

import logging
import time

import requests

log = logging.getLogger("retry")


def get_with_retry(url: str, *, max_retries: int = 5, **kwargs) -> requests.Response:
    """GET with retry-with-backoff on 429 (honoring Retry-After when sent)
    and on transient connection/timeout errors."""
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            if attempt == max_retries:
                raise
            wait = 2 ** (attempt + 1)
            log.warning("connection error on %s (%s), retrying in %ds", url, exc, wait)
            time.sleep(wait)
            continue

        if resp.status_code != 429 or attempt == max_retries:
            resp.raise_for_status()
            return resp
        wait = float(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
        log.warning("rate-limited (429) on %s, retrying in %.0fs", url, wait)
        time.sleep(wait)
    raise RuntimeError("unreachable")  # loop always returns or raises above
