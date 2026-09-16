import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128))
    photo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    @property
    def display_name(self) -> str:
        return self.telegram_username and f"@{self.telegram_username}" or self.first_name


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    cmc_id: Mapped[str] = mapped_column(String(32))
    coingecko_id: Mapped[str] = mapped_column(String(128))
    tier: Mapped[str] = mapped_column(String(32), default="T1")

    # Which criteria provider added this project, and why (free-form; each
    # provider decides what to put here, e.g. {"exchange": "Binance"} or
    # {"tags": ["defi"]}). Lets us support criteria beyond CMC/CG data
    # (exchange listings, tags, any future external source) without schema
    # changes.
    criteria_source: Mapped[str] = mapped_column(String(64), default="manual")
    criteria_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    # False when a criteria sync no longer matches this project. Kept (not
    # deleted) so gap history survives.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    checks: Mapped[list["CompletenessCheck"]] = relationship(
        back_populates="project", order_by="CompletenessCheck.checked_at.desc()"
    )
    gaps: Mapped[list["Gap"]] = relationship(back_populates="project")

    @property
    def latest_check(self) -> "CompletenessCheck | None":
        return self.checks[0] if self.checks else None

    @property
    def open_gaps(self) -> list["Gap"]:
        return [g for g in self.gaps if g.status in (GapStatus.OPEN, GapStatus.MUTED)]


class CompletenessCheck(Base):
    """Raw per-run snapshot, kept for audit/debugging. `Gap` rows are the
    source of truth for what's actionable."""

    __tablename__ = "completeness_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    cmc_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    cg_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    cmc_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cg_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="checks")


class GapStatus(str, enum.Enum):
    OPEN = "OPEN"
    MUTED = "MUTED"
    RESOLVED = "RESOLVED"


class Gap(Base):
    """A tracked data-completeness issue for one (project, field). Opened
    the first time CoinGecko has a field CMC is missing; stays open (with
    repeat reminders) until resolved, muted, or the field reappears on CMC.
    """

    __tablename__ = "gaps"
    __table_args__ = (UniqueConstraint("project_id", "field_name", "opened_at", name="uq_gap_instance"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[GapStatus] = mapped_column(Enum(GapStatus), default=GapStatus.OPEN, index=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_open_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    muted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    last_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="gaps")
    muted_by: Mapped["User | None"] = relationship(foreign_keys=[muted_by_user_id])
    resolved_by: Mapped["User | None"] = relationship(foreign_keys=[resolved_by_user_id])
