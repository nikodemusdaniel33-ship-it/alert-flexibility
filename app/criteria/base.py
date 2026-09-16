from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Candidate:
    """A project a criteria provider says should be tracked."""

    symbol: str
    name: str
    cmc_id: str
    coingecko_id: str
    tier: str = "T1"
    source: str = "manual"
    metadata: dict = field(default_factory=dict)


class CriteriaProvider(Protocol):
    """A pluggable source of projects to track.

    Implementations are not limited to CMC/CoinGecko data — a provider can
    call any external source (an exchange's listing API, a tag/category
    feed, a hand-maintained list, etc). The sync step in
    `app.criteria.sync` only depends on this interface, so adding a new
    criteria source never requires touching the worker or gap logic.
    """

    name: str

    def fetch_candidates(self) -> list[Candidate]: ...
