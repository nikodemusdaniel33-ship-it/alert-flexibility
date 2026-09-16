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
    """Create any missing tables, and heal tables left over from an older
    model shape (create_all only adds tables that don't exist yet, so a
    renamed/added column on an existing table is otherwise silently
    ignored). There's no real data yet, so a stale table is just dropped
    and recreated rather than migrated column-by-column. Once this project
    has real data across a schema change, replace this with Alembic."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    stale = False
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        expected_columns = {c.name for c in table.columns}
        actual_columns = {c["name"] for c in inspector.get_columns(table.name)}
        if not expected_columns.issubset(actual_columns):
            stale = True
            break

    if stale:
        # A single stale table can have FK'd children (e.g. gaps ->
        # projects), so drop everything together rather than fight
        # constraint ordering table-by-table.
        Base.metadata.drop_all(bind=engine)

    Base.metadata.create_all(bind=engine)
