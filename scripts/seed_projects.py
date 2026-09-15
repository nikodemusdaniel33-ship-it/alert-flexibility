"""Upsert projects from config/projects.yaml into the database.

Run manually (`python -m scripts.seed_projects`) after editing the config,
e.g. once you have the real T1-exchange project list.
"""

import yaml

from app.config import settings
from app.db import Base, SessionLocal, engine
from app.models import Project


def run() -> None:
    Base.metadata.create_all(bind=engine)
    with open(settings.projects_config_path) as f:
        data = yaml.safe_load(f) or {}

    db = SessionLocal()
    try:
        for entry in data.get("projects", []):
            existing = (
                db.query(Project).filter(Project.symbol == entry["symbol"]).first()
            )
            if existing:
                existing.name = entry["name"]
                existing.cmc_id = str(entry["cmc_id"])
                existing.coingecko_id = entry["coingecko_id"]
                existing.tier = entry.get("tier", "T1")
            else:
                db.add(
                    Project(
                        symbol=entry["symbol"],
                        name=entry["name"],
                        cmc_id=str(entry["cmc_id"]),
                        coingecko_id=entry["coingecko_id"],
                        tier=entry.get("tier", "T1"),
                    )
                )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    run()
