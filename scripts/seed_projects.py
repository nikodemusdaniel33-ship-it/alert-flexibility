"""Run the criteria sync manually (`python -m scripts.seed_projects`).

Useful after editing config/projects.yaml, without waiting for the next
scheduled worker run. The worker also runs this same sync at the start of
every check.
"""

from app.criteria import sync
from app.db import SessionLocal, ensure_schema


def run() -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        sync.run(db)
    finally:
        db.close()


if __name__ == "__main__":
    run()
