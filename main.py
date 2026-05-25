from __future__ import annotations

import datetime as dt

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

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


app = FastAPI(title="Hållbarhetskollen API")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ui", response_class=HTMLResponse)
def ui_home(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


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
        mapping[(f.category, f.key)] = Factor(
            category=f.category,
            key=f.key,
            unit=f.unit,
            co2e_per_unit=f.co2e_per_unit,
        )

    return mapping


@app.get("/emission-factors", response_model=list[EmissionFactorOut])
def list_factors(db: Session = Depends(get_session)) -> list[EmissionFactor]:
    return list(db.execute(select(EmissionFactor)).scalars().all())


@app.post("/activities", response_model=ActivityOut)
def create_activity(
    payload: ActivityCreate,
    db: Session = Depends(get_session),
) -> ActivityOut:
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
        co2e = None

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
    return week_start, week_start + dt.timedelta(days=6)


@app.get("/reports/weekly", response_model=WeeklyReportOut)
def weekly_report(
    user_id: int = Query(...),
    week_start: dt.date = Query(...),
    db: Session = Depends(get_session),
) -> WeeklyReportOut:
    user = db.get(User, user_id)

    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    start, end = _week_bounds(week_start)

    activities = db.execute(
        select(Activity)
        .where(Activity.user_id == user_id)
        .where(Activity.date >= start)
        .where(Activity.date <= end)
    ).scalars().all()

    factors = _load_factor_map(db)
    total = 0.0

    for a in activities:
        try:
            total += calculate_co2e(a.category, a.key, a.amount, factors)
        except KeyError:
            continue

    return WeeklyReportOut(
        user_id=user_id,
        week_start=start,
        week_end=end,
        total_co2e=total,
    )


@app.get("/ui/users", response_class=HTMLResponse)
def ui_users(request: Request, db: Session = Depends(get_session)):
    users = db.execute(select(User).order_by(User.id.asc())).scalars().all()

    return templates.TemplateResponse(
        request,
        "create_user.html",
        {
            "users": users,
            "message": None,
            "error": None,
        },
    )


@app.post("/ui/users", response_class=HTMLResponse)
def ui_create_user(
    request: Request,
    name: str = Form(""),
    db: Session = Depends(get_session),
):
    users = db.execute(select(User).order_by(User.id.asc())).scalars().all()

    if not name.strip():
        return templates.TemplateResponse(
            request,
            "create_user.html",
            {
                "users": users,
                "message": None,
                "error": "Name får inte vara tomt.",
            },
        )
        
    otillatna_tecken = []
    for tecken in name:
        if not (tecken.isalpha() or tecken.isspace()):
            otillatna_tecken.append(tecken)
    
    if otillatna_tecken:
        unika_otillatna = list(set(otillatna_tecken))
        return templates.TemplateResponse(
            request,
            "create_user.html",
            {
                "users": users,
                "message": None,
                "error": f"Namn får endast innehålla bokstäver och mellanslag",
            },
        )
    
    user = User(name=name.strip())
    db.add(user)
    db.commit()

    users = db.execute(select(User).order_by(User.id.asc())).scalars().all()

    return templates.TemplateResponse(
        request,
        "create_user.html",
        {
            "users": users,
            "message": "User skapad.",
            "error": None,
        },
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

    users = db.execute(select(User).order_by(User.id.asc())).scalars().all()

    return templates.TemplateResponse(
        request,
        "create_user.html",
        {
            "users": users,
            "message": "User borttagen.",
            "error": None,
        },
    )


@app.get("/ui/activities", response_class=HTMLResponse)
def ui_activities(request: Request, db: Session = Depends(get_session)):
    users = db.execute(select(User).order_by(User.id.asc())).scalars().all()
    activities = db.execute(select(Activity).order_by(Activity.id.asc())).scalars().all()
    factors = db.execute(
        select(EmissionFactor).order_by(EmissionFactor.category.asc())
    ).scalars().all()

    return templates.TemplateResponse(
        request,
        "activities.html",
        {
            "users": users,
            "activities": activities,
            "factors": factors,
            "message": None,
            "error": None,
        },
    )


@app.post("/ui/activities", response_class=HTMLResponse)
def ui_create_activity(
    request: Request,
    user_id: int = Form(...),
    category: str = Form(""),
    key: str = Form(""),
    amount: float | None = Form(None),
    date: str = Form(""),
    db: Session = Depends(get_session),
):
    users = db.execute(select(User).order_by(User.id.asc())).scalars().all()
    activities = db.execute(select(Activity).order_by(Activity.id.asc())).scalars().all()
    factors = db.execute(
        select(EmissionFactor).order_by(EmissionFactor.category.asc())
    ).scalars().all()

    if not category.strip() or not key.strip() or amount is None or not date.strip():
        return templates.TemplateResponse(
            request,
            "activities.html",
            {
                "users": users,
                "activities": activities,
                "factors": factors,
                "message": None,
                "error": "Alla fält måste fyllas i.",
            },
        )

    try:
        parsed_date = dt.date.fromisoformat(date)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "activities.html",
            {
                "users": users,
                "activities": activities,
                "factors": factors,
                "message": None,
                "error": "Fel datumformat.",
            },
        )

    activity = Activity(
        user_id=user_id,
        category=category.strip(),
        key=key.strip(),
        amount=amount,
        date=parsed_date,
    )

    db.add(activity)
    db.commit()

    activities = db.execute(select(Activity).order_by(Activity.id.asc())).scalars().all()
    user = db.get(User, user_id)

    return templates.TemplateResponse(
        request,
        "activities.html",
        {
            "users": users,
            "activities": activities,
            "factors": factors,
            "message": f"Loggad aktivitet för: {user.name if user else user_id}",
            "error": None,
        },
    )


@app.get("/ui/reports/weekly", response_class=HTMLResponse)
def ui_weekly_report(
    request: Request,
    user_id: int | None = None,
    week_start: str | None = None,
    db: Session = Depends(get_session),
):
    users = db.execute(select(User).order_by(User.id.asc())).scalars().all()

    total = None
    err = None
    rows = []

    if user_id is not None and week_start:
        user = db.get(User, user_id)

        if not user:
            err = "Användaren finns inte."
        else:
            try:
                start = dt.date.fromisoformat(week_start)
                end = start + dt.timedelta(days=6)
            except ValueError:
                err = "Fel datumformat."
            else:
                activities = db.execute(
                    select(Activity)
                    .where(Activity.user_id == user_id)
                    .where(Activity.date >= start)
                    .where(Activity.date <= end)
                    .order_by(Activity.date.asc())
                ).scalars().all()

                factors = _load_factor_map(db)
                total = 0.0

                for a in activities:
                    try:
                        co2e = calculate_co2e(
                            a.category,
                            a.key,
                            a.amount,
                            factors,
                        )
                        total += co2e
                    except KeyError:
                        co2e = None

                    rows.append(
                        {
                            "activity": a,
                            "co2e": co2e,
                        }
                    )

    return templates.TemplateResponse(
        request,
        "weekly.html",
        {
            "users": users,
            "total": total,
            "err": err,
            "rows": rows,
            "selected_user_id": user_id,
            "selected_week_start": week_start,
        },
    )
