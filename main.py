from __future__ import annotations

import datetime as dt
from typing import Dict, Tuple

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request

from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import Base

from .db import get_session
from .models import Activity, EmissionFactor, User
from .schemas import (
    ActivityCreate,
    ActivityOut,
    EmissionFactorOut,
    UserCreate,
    UserOut,
    WeeklyReportOut,
)
from .services.emissions import Factor, FactorMap, calculate_co2e

app = FastAPI(title="Hållbarhetskollen API (starter)")
templates = Jinja2Templates(directory="templates")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/users", response_model=UserOut)
def create_user(payload: UserCreate, db: Session = Depends(get_session)) -> User:
    user = User(name=payload.name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_session)) -> list[User]:
    return list(db.execute(select(User)).scalars().all())


def _load_factor_map(db: Session) -> FactorMap:
    factors = db.execute(select(EmissionFactor)).scalars().all()
    mapping: FactorMap = {}
    for f in factors:
        mapping[(f.category, f.key)] = Factor(category=f.category, key=f.key, unit=f.unit, co2e_per_unit=f.co2e_per_unit)
    return mapping


@app.get("/emission-factors", response_model=list[EmissionFactorOut])
def list_factors(db: Session = Depends(get_session)) -> list[EmissionFactor]:
    return list(db.execute(select(EmissionFactor)).scalars().all())


@app.post("/activities", response_model=ActivityOut)
def create_activity(payload: ActivityCreate, db: Session = Depends(get_session)) -> ActivityOut:
    user = db.get(User, payload.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    activity = Activity(
        user_id=payload.user_id,
        category=payload.category,
        key=payload.key,
        amount=payload.amount,
        date=payload.date,
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)

    factors = _load_factor_map(db)
    try:
        co2e = calculate_co2e(activity.category, activity.key, activity.amount, factors)
    except KeyError:
        co2e = None  # För starter: ok att returnera None; i projektet bör ni hantera detta bättre.

    return ActivityOut(
        id=activity.id,
        user_id=activity.user_id,
        category=activity.category,
        key=activity.key,
        amount=activity.amount,
        date=activity.date,
        co2e=co2e,
    )


@app.get("/activities", response_model=list[ActivityOut])
def list_activities(
    user_id: int | None = Query(default=None),
    db: Session = Depends(get_session),
) -> list[ActivityOut]:
    stmt = select(Activity)
    if user_id is not None:
        stmt = stmt.where(Activity.user_id == user_id)

    activities = list(db.execute(stmt).scalars().all())
    factors = _load_factor_map(db)

    out: list[ActivityOut] = []
    for a in activities:
        try:
            co2e = calculate_co2e(a.category, a.key, a.amount, factors)
        except KeyError:
            co2e = None
        out.append(
            ActivityOut(
                id=a.id,
                user_id=a.user_id,
                category=a.category,
                key=a.key,
                amount=a.amount,
                date=a.date,
                co2e=co2e,
            )
        )
    return out


def _week_bounds(week_start: dt.date) -> tuple[dt.date, dt.date]:
    # week_start antas vara måndag; i projektet kan ni validera/normalisera.
    return week_start, week_start + dt.timedelta(days=6)


@app.get("/reports/weekly", response_model=WeeklyReportOut)
def weekly_report(
    user_id: int = Query(...),
    week_start: dt.date = Query(..., description="Veckans startdatum (måndag)"),
    db: Session = Depends(get_session),
) -> WeeklyReportOut:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    start, end = _week_bounds(week_start)

    stmt = (
        select(Activity)
        .where(Activity.user_id == user_id)
        .where(Activity.date >= start)
        .where(Activity.date <= end)
    )
    activities = list(db.execute(stmt).scalars().all())
    factors = _load_factor_map(db)

    total = 0.0
    for a in activities:
        try:
            total += calculate_co2e(a.category, a.key, a.amount, factors)
        except KeyError:
            # I projektet: bestäm hur “okända faktorer” ska hanteras
            continue

    return WeeklyReportOut(user_id=user_id, week_start=start, week_end=end, total_co2e=total)
#roliga grejer
@app.get("/ui", response_class=HTMLResponse)
def ui_home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"request": request}
    )


@app.get("/ui/users", response_class=HTMLResponse)
def ui_users(request: Request, db: Session = Depends(get_session)):
    users = db.execute(select(User)).scalars().all()

    return templates.TemplateResponse(
        request,
        "create_user.html",
        {
            "request": request,
            "users": users,
            "message": None,
            "error": None,
        }
    )


@app.post("/ui/users", response_class=HTMLResponse)
def ui_create_user(
    request: Request,
    name: str = Form(None),
    db: Session = Depends(get_session),
):
    if not name or name.strip() == "":
        users = db.execute(select(User)).scalars().all()

        return templates.TemplateResponse(
            request,
            "create_user.html",
            {
                "request": request,
                "users": users,
                "message": None,
                "error": "Name får inte vara tomt.",
            }
        )

    db.add(User(name=name.strip()))
    db.commit()

    users = db.execute(select(User)).scalars().all()

    return templates.TemplateResponse(
        request,
        "create_user.html",
        {
            "request": request,
            "users": users,
            "message": "User skapad",
            "error": None,
        }
    )


@app.post("/ui/users/{user_id}/delete", response_class=HTMLResponse)
def ui_delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    user = db.get(User, user_id)

    if user:
        db.delete(user)
        db.commit()

    users = db.execute(select(User)).scalars().all()

    return templates.TemplateResponse(
        request,
        "create_user.html",
        {
            "request": request,
            "users": users,
            "message": "User borttagen",
            "error": None,
        }
    )
#activities grejer
@app.get("/ui/activities", response_class=HTMLResponse)
def ui_activities(request: Request, db: Session = Depends(get_session)):
    users = db.execute(select(User)).scalars().all()
    activities = db.execute(select(Activity)).scalars().all()

    return templates.TemplateResponse(
        request,
        "activities.html",
        {
            "users": users,
            "activities": activities,
        }
    )


@app.post("/ui/activities", response_class=HTMLResponse)
def ui_create_activity(
    request: Request,
    user_id: int = Form(...),
    category: str = Form(...),
    key: str = Form(...),
    amount: float = Form(...),
    date: str = Form(...),
    db: Session = Depends(get_session),
):
    activity = Activity(
        user_id=user_id,
        category=category,
        key=key,
        amount=amount,
        date=dt.date.fromisoformat(date),
    )

    db.add(activity)
    db.commit()

    users = db.execute(select(User)).scalars().all()
    activities = db.execute(select(Activity)).scalars().all()

    return templates.TemplateResponse(
        request,
        "activities.html",
        {
            "users": users,
            "activities": activities,
        }
    )