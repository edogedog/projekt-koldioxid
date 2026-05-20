from __future__ import annotations

import datetime as dt


def test_create_user_and_activity_and_weekly_report(client):
    # create user
    r = client.post("/users", json={"name": "Ada"})
    assert r.status_code == 200
    user_id = r.json()["id"]

    # create activity: 10 km car
    r = client.post(
        "/activities",
        json={
            "user_id": user_id,
            "category": "travel",
            "key": "car",
            "amount": 10,
            "date": "2026-02-02",  # Monday (example)
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["co2e"] is not None

    # weekly report starting 2026-02-02
    r = client.get("/reports/weekly", params={"user_id": user_id, "week_start": "2026-02-02"})
    assert r.status_code == 200
    report = r.json()
    assert report["total_co2e"] > 0
