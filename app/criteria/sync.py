import logging

from sqlalchemy.orm import Session

from app.criteria.registry import enabled_providers
from app.models import Project

log = logging.getLogger("criteria.sync")


def run(db: Session) -> None:
    """Upsert projects matching any enabled criteria provider; deactivate
    (never delete) projects no provider currently matches, so gap history
    is preserved if a project comes back later."""

    seen_keys: set[tuple[str, str]] = set()

    for provider in enabled_providers():
        try:
            candidates = provider.fetch_candidates()
        except Exception:
            log.exception("Criteria provider %s failed", provider.name)
            continue

        for candidate in candidates:
            key = (candidate.cmc_id, candidate.coingecko_id)
            seen_keys.add(key)

            existing = (
                db.query(Project)
                .filter(
                    Project.cmc_id == candidate.cmc_id,
                    Project.coingecko_id == candidate.coingecko_id,
                )
                .first()
            )
            if existing:
                existing.symbol = candidate.symbol
                existing.name = candidate.name
                existing.tier = candidate.tier
                existing.criteria_source = candidate.source
                existing.criteria_metadata = candidate.metadata
                existing.is_active = True
            else:
                db.add(
                    Project(
                        symbol=candidate.symbol,
                        name=candidate.name,
                        cmc_id=candidate.cmc_id,
                        coingecko_id=candidate.coingecko_id,
                        tier=candidate.tier,
                        criteria_source=candidate.source,
                        criteria_metadata=candidate.metadata,
                        is_active=True,
                    )
                )

    db.flush()

    for project in db.query(Project).filter(Project.is_active.is_(True)).all():
        if (project.cmc_id, project.coingecko_id) not in seen_keys:
            project.is_active = False

    db.commit()
