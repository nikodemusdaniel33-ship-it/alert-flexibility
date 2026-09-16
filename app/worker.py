"""One-shot check run: sync tracked projects, fetch CMC + CoinGecko data,
open/reminder/resolve gaps, and alert on Telegram.

Meant to be invoked periodically (e.g. Railway's cron schedule), not run as
a long-lived process.
"""

import logging
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy.orm import Session

from app.clients import cmc, coingecko
from app.compare import TRACKED_FIELDS
from app.config import settings
from app.criteria import sync as criteria_sync
from app.db import SessionLocal, ensure_schema
from app.models import CompletenessCheck, Gap, GapStatus, Project
from app.telegram import send_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")


def _reminder_interval() -> timedelta:
    return timedelta(hours=settings.gap_reminder_interval_hours)


def _aware_utc(dt: datetime | None) -> datetime | None:
    """Some DB backends (SQLite) hand back naive datetimes even for
    tz-aware columns. Every timestamp we write is UTC, so treat a naive
    value as UTC rather than letting naive/aware comparisons blow up."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _open_gap(db: Session, project: Project, field: str, now: datetime) -> Gap:
    gap = Gap(project_id=project.id, field_name=field, status=GapStatus.OPEN, opened_at=now, last_seen_open_at=now)
    db.add(gap)
    db.flush()
    send_alert(f"*New data gap*: {project.name} ({project.symbol})\nCMC is missing: `{field}`")
    gap.last_alert_at = now
    gap.alert_count = 1
    return gap


def _handle_field(db: Session, project: Project, field: str, is_missing: bool, now: datetime) -> None:
    existing = (
        db.query(Gap)
        .filter(
            Gap.project_id == project.id,
            Gap.field_name == field,
            Gap.status.in_([GapStatus.OPEN, GapStatus.MUTED]),
        )
        .first()
    )

    if not is_missing:
        if existing:
            existing.status = GapStatus.RESOLVED
            existing.resolved_at = now
            existing.resolution_note = "auto-resolved: no longer missing on CMC"
            send_alert(
                f"*Gap auto-resolved*: {project.name} ({project.symbol}) — `{field}` "
                f"now present on CMC"
            )
        return

    if not existing:
        _open_gap(db, project, field, now)
        return

    existing.last_seen_open_at = now

    if existing.status == GapStatus.MUTED:
        if _aware_utc(existing.muted_until) and _aware_utc(existing.muted_until) <= now:
            existing.status = GapStatus.OPEN
            existing.muted_until = None
            existing.last_alert_at = now
            existing.alert_count += 1
            send_alert(
                f"*Gap reactivated*: {project.name} ({project.symbol}) — `{field}` "
                f"is still missing on CMC"
            )
        return

    # OPEN and unmuted: repeat reminder on a fixed cadence.
    last_alert_at = _aware_utc(existing.last_alert_at)
    if last_alert_at is None or now - last_alert_at >= _reminder_interval():
        existing.last_alert_at = now
        existing.alert_count += 1
        send_alert(
            f"*Reminder — still missing*: {project.name} ({project.symbol}) — `{field}` "
            f"(open {existing.alert_count} alerts)"
        )


def check_project(db: Session, project: Project) -> None:
    now = datetime.now(timezone.utc)
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

    db.add(
        CompletenessCheck(
            project_id=project.id,
            cmc_fields={k: v for k, v in cmc_fields.items() if k != "_raw"},
            cg_fields={k: v for k, v in cg_fields.items() if k != "_raw"},
            cmc_error=cmc_error,
            cg_error=cg_error,
        )
    )

    if cmc_error or cg_error:
        # Can't tell what's actually missing right now; don't touch gap state.
        db.commit()
        return

    for field in TRACKED_FIELDS:
        is_missing = bool(cg_fields.get(field)) and not cmc_fields.get(field)
        _handle_field(db, project, field, is_missing, now)

    db.commit()


def run() -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        criteria_sync.run(db)

        projects = db.query(Project).filter(Project.is_active.is_(True)).all()
        log.info("Checking %d tracked projects", len(projects))
        for project in projects:
            check_project(db, project)
    finally:
        db.close()


if __name__ == "__main__":
    run()
