from datetime import datetime, timedelta, timezone

import requests
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import (
    SESSION_COOKIE,
    TelegramAuthError,
    create_session_cookie,
    get_current_user,
    get_optional_user,
    verify_telegram_auth,
)
from app.config import settings
from app.criteria.market_universe import fetch_cg_detail_raw, fetch_cmc_detail_raw
from app.db import ensure_schema, get_db
from app.market_data.models import CmcBinanceListed, CmcTop600
from app.models import Gap, GapStatus, Project, User
from app.telegram import send_alert
from scripts.compare_coin_detail import _fmt, build_rows

ensure_schema()

app = FastAPI(title="alert-flexibility")
templates = Jinja2Templates(directory="templates")


def _pretty_field_name(field_name: str) -> str:
    """"social:website" -> "Website"; "contract:optimistic-ethereum" ->
    "Contract - Optimistic Ethereum". Falls back to the raw name for
    anything that isn't in the "category:detail" shape."""
    if ":" not in field_name:
        return field_name
    category, detail = field_name.split(":", 1)
    label = detail.replace("-", " ").replace("_", " ").title()
    prefixes = {"social": "", "contract": "Contract - ", "explorer": "Explorer - "}
    return f"{prefixes.get(category, category.title() + ' - ')}{label}"


templates.env.filters["pretty_field"] = _pretty_field_name


@app.get("/login")
def login_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(
        "login.html", {"request": request, "bot_username": settings.telegram_bot_username}
    )


@app.get("/auth/telegram/callback")
def telegram_callback(request: Request, db: Session = Depends(get_db)):
    data = dict(request.query_params)
    try:
        verify_telegram_auth(data)
    except TelegramAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    telegram_id = int(data["id"])
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user:
        user.telegram_username = data.get("username")
        user.first_name = data.get("first_name", user.first_name)
        user.photo_url = data.get("photo_url")
    else:
        user = User(
            telegram_id=telegram_id,
            telegram_username=data.get("username"),
            first_name=data.get("first_name", "Telegram user"),
            photo_url=data.get("photo_url"),
        )
        db.add(user)
    db.commit()
    db.refresh(user)

    response = RedirectResponse(url="/")
    response.set_cookie(
        SESSION_COOKIE, create_session_cookie(user.id), httponly=True, samesite="lax", max_age=30 * 24 * 3600
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/")
def dashboard(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = (
        db.query(Project)
        .filter(Project.is_active.is_(True))
        .order_by(Project.symbol)
        .all()
    )
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "projects": projects, "user": user, "GapStatus": GapStatus}
    )


@app.get("/projects/{project_id}/detail")
def project_detail(
    project_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    error = None
    sections: list[dict] = []
    try:
        raw_cmc = fetch_cmc_detail_raw(int(project.cmc_id))
        raw_cg = fetch_cg_detail_raw(project.coingecko_id, market_data=True, community_data=True)
    except (requests.RequestException, ValueError) as exc:
        error = str(exc)
    else:
        current_section = None
        for row in build_rows(raw_cmc, raw_cg):
            if row[0] == "section":
                current_section = {"title": row[1], "rows": []}
                sections.append(current_section)
                continue
            info, field_cmc, field_cg, data_cmc, data_cg = row
            current_section["rows"].append(
                {
                    "info": info,
                    "field_cmc": field_cmc,
                    "field_cg": field_cg,
                    "data_cmc": _fmt(data_cmc),
                    "data_cg": _fmt(data_cg),
                }
            )

    return templates.TemplateResponse(
        "project_detail.html",
        {"request": request, "project": project, "sections": sections, "error": error, "user": user},
    )


@app.get("/market-data")
def market_data_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    top600 = db.query(CmcTop600).order_by(CmcTop600.cmc_rank).all()
    binance_listed = db.query(CmcBinanceListed).order_by(CmcBinanceListed.cmc_rank.is_(None), CmcBinanceListed.cmc_rank).all()
    return templates.TemplateResponse(
        "market_data.html",
        {"request": request, "user": user, "top600": top600, "binance_listed": binance_listed},
    )


@app.get("/api/projects")
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = db.query(Project).filter(Project.is_active.is_(True)).order_by(Project.symbol).all()
    result = []
    for p in projects:
        latest = p.latest_check
        result.append(
            {
                "id": p.id,
                "symbol": p.symbol,
                "name": p.name,
                "tier": p.tier,
                "criteria_source": p.criteria_source,
                "last_checked_at": latest.checked_at if latest else None,
                "cmc_error": latest.cmc_error if latest else None,
                "cg_error": latest.cg_error if latest else None,
                "gaps": [
                    {
                        "id": g.id,
                        "field": g.field_name,
                        "status": g.status.value,
                        "muted_until": g.muted_until,
                        "alert_count": g.alert_count,
                    }
                    for g in p.open_gaps
                ],
            }
        )
    return result


def _get_open_gap(db: Session, gap_id: int) -> Gap:
    gap = db.get(Gap, gap_id)
    if not gap or gap.status not in (GapStatus.OPEN, GapStatus.MUTED):
        raise HTTPException(status_code=404, detail="gap not found or already resolved")
    return gap


@app.post("/gaps/{gap_id}/resolve")
def resolve_gap(
    gap_id: int,
    note: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    gap = _get_open_gap(db, gap_id)
    gap.status = GapStatus.RESOLVED
    gap.resolved_at = datetime.now(timezone.utc)
    gap.resolved_by_user_id = user.id
    gap.resolution_note = note.strip() or None
    db.commit()

    send_alert(
        f"*Gap resolved*: {gap.project.name} ({gap.project.symbol}) — `{gap.field_name}`\n"
        f"Marked done by {user.display_name}"
    )
    return RedirectResponse(url="/", status_code=303)


@app.post("/gaps/{gap_id}/mute")
def mute_gap(
    gap_id: int,
    hours: int | None = Form(None),
    until: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    gap = _get_open_gap(db, gap_id)

    if until:
        muted_until = datetime.fromisoformat(until)
        if muted_until.tzinfo is None:
            muted_until = muted_until.replace(tzinfo=timezone.utc)
    elif hours:
        muted_until = datetime.now(timezone.utc) + timedelta(hours=hours)
    else:
        raise HTTPException(status_code=400, detail="provide hours or until")

    gap.status = GapStatus.MUTED
    gap.muted_until = muted_until
    gap.muted_by_user_id = user.id
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/gaps/{gap_id}/reactivate")
def reactivate_gap(
    gap_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    gap = _get_open_gap(db, gap_id)
    gap.status = GapStatus.OPEN
    gap.muted_until = None
    db.commit()
    return RedirectResponse(url="/", status_code=303)
