from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings


def _normalize_url(url: str) -> str:
    # Railway's Postgres plugin hands out postgres:// which SQLAlchemy's
    # psycopg2 dialect no longer accepts.
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url


engine = create_engine(_normalize_url(settings.database_url), pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    """Create any missing tables. Does NOT auto-heal an existing table
    whose columns no longer match its model -- this function used to
    drop_all() the *entire* database (every table, not just the mismatched
    one) whenever any single table looked stale, on the reasoning that
    there was no real data yet to lose. That stopped being true once
    `users`/`projects`/`gaps` held real Telegram-linked accounts and alert
    history; a silent full-database wipe triggered by an unrelated
    reference-data column addition is not an acceptable failure mode
    (see git history on this function for the original, since-obsolete
    reasoning). A mismatch now raises instead of guessing -- whoever is
    making the schema change picks the migration explicitly (an ALTER
    TABLE for an additive change; a deliberate, reviewed reset for a
    genuinely destructive one) rather than every script silently deciding
    for them at startup. This is still not a real migration tool -- once
    this project needs more than additive column changes, replace it with
    Alembic."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    mismatches: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        expected_columns = {c.name for c in table.columns}
        actual_columns = {c["name"] for c in inspector.get_columns(table.name)}
        missing = expected_columns - actual_columns
        if missing:
            mismatches.append(f"{table.name} is missing column(s): {', '.join(sorted(missing))}")

    if mismatches:
        raise RuntimeError(
            "Database schema is out of date with the current models:\n  "
            + "\n  ".join(mismatches)
            + "\nAdd the missing column(s) with an explicit ALTER TABLE (or a deliberate, reviewed reset "
            "if the change is genuinely destructive) before running this again -- this no longer "
            "auto-drops tables to fix a mismatch."
        )

    Base.metadata.create_all(bind=engine)
