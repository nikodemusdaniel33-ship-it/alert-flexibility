import yaml

from app.criteria.base import Candidate
from app.config import settings


class ManualListProvider:
    """Reads the hand-maintained project list from config/projects.yaml.

    Placeholder provider until real criteria (exchange listings, tags,
    other external sources) are wired up as their own providers.
    """

    name = "manual"

    def __init__(self, path: str | None = None):
        self.path = path or settings.projects_config_path

    def fetch_candidates(self) -> list[Candidate]:
        with open(self.path) as f:
            data = yaml.safe_load(f) or {}

        return [
            Candidate(
                symbol=entry["symbol"],
                name=entry["name"],
                cmc_id=str(entry["cmc_id"]),
                coingecko_id=entry["coingecko_id"],
                tier=entry.get("tier", "T1"),
                source="manual",
                metadata={k: v for k, v in entry.items() if k not in {"symbol", "name", "cmc_id", "coingecko_id", "tier"}},
            )
            for entry in data.get("projects", [])
        ]
