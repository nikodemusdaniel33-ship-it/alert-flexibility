from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db
from app.models import Project

Base.metadata.create_all(bind=engine)

app = FastAPI(title="alert-flexibility")
templates = Jinja2Templates(directory="templates")


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.symbol).all()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "projects": projects}
    )


@app.get("/api/projects")
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.symbol).all()
    result = []
    for p in projects:
        latest = p.latest_check
        result.append(
            {
                "id": p.id,
                "symbol": p.symbol,
                "name": p.name,
                "tier": p.tier,
                "is_muted": p.is_muted,
                "pic": p.pic,
                "checked_at": p.checked_at,
                "follow_up_notes": p.follow_up_notes,
                "missing_in_cmc": latest.missing_in_cmc if latest else [],
                "last_checked_at": latest.checked_at if latest else None,
                "cmc_error": latest.cmc_error if latest else None,
                "cg_error": latest.cg_error if latest else None,
            }
        )
    return result


@app.post("/projects")
def add_project(
    symbol: str = Form(...),
    name: str = Form(...),
    cmc_id: str = Form(...),
    coingecko_id: str = Form(...),
    tier: str = Form("T1"),
    db: Session = Depends(get_db),
):
    db.add(
        Project(
            symbol=symbol.upper().strip(),
            name=name.strip(),
            cmc_id=cmc_id.strip(),
            coingecko_id=coingecko_id.strip(),
            tier=tier.strip() or "T1",
        )
    )
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/projects/{project_id}/check")
def mark_checked(
    project_id: int,
    pic: str = Form(...),
    follow_up_notes: str = Form(""),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project:
        project.pic = pic.strip()
        project.follow_up_notes = follow_up_notes.strip() or None
        project.checked_at = datetime.now(timezone.utc)
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/projects/{project_id}/mute")
def toggle_mute(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project:
        project.is_muted = not project.is_muted
        db.commit()
    return RedirectResponse(url="/", status_code=303)
