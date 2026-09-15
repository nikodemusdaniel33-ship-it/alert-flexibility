"""One-shot check run: fetch CMC + CoinGecko data for every tracked project,
compare completeness, persist a snapshot, and alert on new gaps.

Meant to be invoked periodically (e.g. Railway's cron schedule), not run as a
long-lived process.
"""

import logging

import requests
from sqlalchemy.orm import Session

from app.clients import cmc, coingecko
from app.compare import missing_in_cmc
from app.db import Base, SessionLocal, engine
from app.models import CompletenessCheck, Project
from app.telegram import send_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")


def check_project(db: Session, project: Project) -> None:
    cmc_fields: dict = {}
    cg_fields: dict = {}
    cmc_error = None
    cg_error = None

    try:
        cmc_fields = cmc.fetch_socials(project.cmc_id)
    except requests.RequestException as exc:
        cmc_error = str(exc)
        log.warning("CMC fetch failed for %s: %s", project.symbol, exc)

    try:
        cg_fields = coingecko.fetch_socials(project.coingecko_id)
    except requests.RequestException as exc:
        cg_error = str(exc)
        log.warning("CoinGecko fetch failed for %s: %s", project.symbol, exc)

    gaps = missing_in_cmc(cmc_fields, cg_fields) if not cmc_error and not cg_error else []
    previous = project.latest_check
    previously_flagged = set(previous.missing_in_cmc) if previous else set()

    check = CompletenessCheck(
        project_id=project.id,
        cmc_fields={k: v for k, v in cmc_fields.items() if k != "_raw"},
        cg_fields={k: v for k, v in cg_fields.items() if k != "_raw"},
        missing_in_cmc=gaps,
        cmc_error=cmc_error,
        cg_error=cg_error,
    )
    db.add(check)
    db.commit()

    new_gaps = set(gaps) - previously_flagged
    if new_gaps and not project.is_muted:
        send_alert(
            f"*Data gap on CoinMarketCap*: {project.name} ({project.symbol})\n"
            f"Missing vs CoinGecko: {', '.join(sorted(new_gaps))}"
        )
        log.info("Alerted on %s for new gaps: %s", project.symbol, new_gaps)


def run() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        projects = db.query(Project).all()
        log.info("Checking %d tracked projects", len(projects))
        for project in projects:
            check_project(db, project)
    finally:
        db.close()


if __name__ == "__main__":
    run()
