from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    cmc_id: Mapped[str] = mapped_column(String(32))
    coingecko_id: Mapped[str] = mapped_column(String(128))
    tier: Mapped[str] = mapped_column(String(32), default="T1")

    is_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    pic: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    follow_up_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    checks: Mapped[list["CompletenessCheck"]] = relationship(
        back_populates="project", order_by="CompletenessCheck.checked_at.desc()"
    )

    @property
    def latest_check(self) -> "CompletenessCheck | None":
        return self.checks[0] if self.checks else None


class CompletenessCheck(Base):
    __tablename__ = "completeness_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    cmc_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    cg_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    missing_in_cmc: Mapped[list] = mapped_column(JSON, default=list)
    cmc_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cg_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="checks")

    @property
    def has_gap(self) -> bool:
        return bool(self.missing_in_cmc)
