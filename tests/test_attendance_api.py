from datetime import date, timedelta

import pytest
import redis
from test_user_api import P, auth, client, db, make_user  # noqa: F401  (fixtures + helpers)

from api.services import attendance as svc
from database.models import Role


@pytest.fixture
def faces(monkeypatch):
    """Fake of the Redis face-verification flags: faces.add(user_id) simulates the inference service."""
    flags: dict[int, float] = {}

    class Fake:
        add = staticmethod(lambda uid, conf=0.9: flags.__setitem__(uid, conf))

    monkeypatch.setattr(svc.face_verification, "peek", lambda uid: flags.get(uid))
    monkeypatch.setattr(svc.face_verification, "consume", lambda uid: flags.pop(uid, None))
    return Fake


def test_status_buttons_follow_face_and_sequence(client, faces):
    uid = make_user("s@x.com")
    h = auth(client, "s@x.com")
    s = client.get(f"{P}/attendance/me/status", headers=h).json()
    assert (s["face_verified"], s["can_check_in"], s["can_check_out"]) == (False, False, False)

    faces.add(uid)
    s = client.get(f"{P}/attendance/me/status", headers=h).json()
    assert (s["face_verified"], s["can_check_in"], s["can_check_out"]) == (True, True, False)

    assert client.post(f"{P}/attendance/check-in", headers=h).status_code == 201
    s = client.get(f"{P}/attendance/me/status", headers=h).json()
    assert s["is_checked_in"] and not s["can_check_in"] and not s["can_check_out"]  # face consumed

    faces.add(uid)
    s = client.get(f"{P}/attendance/me/status", headers=h).json()
    assert (s["can_check_in"], s["can_check_out"]) == (False, True)


def test_requires_verified_face_and_consumes_it(client, faces):
    uid = make_user("s@x.com")
    h = auth(client, "s@x.com")
    assert client.post(f"{P}/attendance/check-in", headers=h).status_code == 403
    faces.add(uid)
    r = client.post(f"{P}/attendance/check-in", headers=h)
    assert r.status_code == 201 and r.json()["event_type"] == "check_in" and r.json()["face_confidence"] == 0.9
    assert r.json()["occurred_at"].endswith("Z")
    assert client.post(f"{P}/attendance/check-out", headers=h).status_code == 403  # recognition is single-use
    faces.add(uid)
    assert client.post(f"{P}/attendance/check-out", headers=h).status_code == 201


def test_sequence_is_enforced_without_burning_the_face(client, faces):
    uid = make_user("s@x.com")
    h = auth(client, "s@x.com")
    faces.add(uid)
    assert client.post(f"{P}/attendance/check-out", headers=h).status_code == 409
    assert client.post(f"{P}/attendance/check-in", headers=h).status_code == 201
    faces.add(uid)
    assert client.post(f"{P}/attendance/check-in", headers=h).status_code == 409
    assert client.get(f"{P}/attendance/me/status", headers=h).json()["face_verified"] is True


def test_first_check_in_and_last_check_out_per_day(client, faces):
    uid = make_user("s@x.com")
    h = auth(client, "s@x.com")
    ids = []
    for action in ("check-in", "check-out", "check-in", "check-out"):
        faces.add(uid)
        r = client.post(f"{P}/attendance/{action}", headers=h)
        assert r.status_code == 201
        ids.append(r.json())
    day = client.get(f"{P}/attendance/me", headers=h).json()["items"]
    assert len(day) == 1 and set(day[0]) == {"user_id", "work_date", "first_check_in", "last_check_out"}
    assert day[0]["first_check_in"] == ids[0]["occurred_at"]
    assert day[0]["last_check_out"] == ids[3]["occurred_at"]
    st = client.get(f"{P}/attendance/me/status", headers=h).json()
    assert st["first_check_in"] == ids[0]["occurred_at"] and st["last_check_out"] == ids[3]["occurred_at"]


def test_days_are_separate_and_new_day_resets_sequence(client, faces, monkeypatch):
    uid = make_user("s@x.com")
    h = auth(client, "s@x.com")
    d1 = date(2026, 3, 10)
    monkeypatch.setattr(svc, "today", lambda: d1)
    faces.add(uid)
    assert client.post(f"{P}/attendance/check-in", headers=h).status_code == 201  # forgot to check out
    monkeypatch.setattr(svc, "today", lambda: d1 + timedelta(days=1))
    assert client.get(f"{P}/attendance/me/status", headers=h).json()["is_checked_in"] is False
    faces.add(uid)
    assert client.post(f"{P}/attendance/check-in", headers=h).status_code == 201
    days = client.get(f"{P}/attendance/me", headers=h, params={"date_from": "2026-03-01"}).json()["items"]
    assert [d["work_date"] for d in days] == ["2026-03-11", "2026-03-10"]
    assert days[1]["last_check_out"] is None


def test_staff_sees_only_own_and_cannot_use_admin_endpoints(client, faces):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    ha, hb = auth(client, "a@x.com"), auth(client, "b@x.com")
    faces.add(a)
    client.post(f"{P}/attendance/check-in", headers=ha)
    assert client.get(f"{P}/attendance/me", headers=hb).json()["items"] == []
    assert len(client.get(f"{P}/attendance/me", headers=ha).json()["items"]) == 1
    assert client.get(f"{P}/attendance/records", headers=ha).status_code == 403
    assert client.get(f"{P}/attendance/events", headers=ha).status_code == 403
    assert client.get(f"{P}/attendance/me/status").status_code == 401


def test_system_role_cannot_mark(client, faces):
    uid = make_user("sys@x.com", Role.system)
    h = auth(client, "sys@x.com")
    faces.add(uid)
    assert client.post(f"{P}/attendance/check-in", headers=h).status_code == 403


def test_admin_sees_everything(client, faces):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    make_user("admin@x.com", Role.admin)
    ha, hb, hadmin = auth(client, "a@x.com"), auth(client, "b@x.com"), auth(client, "admin@x.com")
    for uid, h, action in [(a, ha, "check-in"), (a, ha, "check-out"), (a, ha, "check-in"), (b, hb, "check-in")]:
        faces.add(uid)
        assert client.post(f"{P}/attendance/{action}", headers=h).status_code == 201

    rec = client.get(f"{P}/attendance/records", headers=hadmin).json()
    assert rec["total"] == 2
    row_a = next(r for r in rec["items"] if r["user_id"] == a)
    assert (row_a["check_in_count"], row_a["check_out_count"], row_a["email"]) == (2, 1, "a@x.com")
    assert client.get(f"{P}/attendance/records", headers=hadmin, params={"user_id": b}).json()["total"] == 1

    ev = client.get(f"{P}/attendance/events", headers=hadmin).json()
    assert ev["total"] == 4 and ev["items"][0]["user_id"] == b  # newest first
    assert client.get(f"{P}/attendance/events", headers=hadmin, params={"user_id": a, "limit": 2}).json()["total"] == 3


def test_date_range_validation(client):
    make_user("s@x.com")
    h = auth(client, "s@x.com")
    r = client.get(f"{P}/attendance/me", headers=h, params={"date_from": "2026-02-01", "date_to": "2026-01-01"})
    assert r.status_code == 422
    r = client.get(f"{P}/attendance/me", headers=h, params={"date_from": "2020-01-01", "date_to": "2026-01-01"})
    assert r.status_code == 422


def test_redis_down_fails_closed(client, monkeypatch):
    make_user("s@x.com")
    h = auth(client, "s@x.com")

    def boom(_uid):
        raise redis.ConnectionError("down")

    monkeypatch.setattr(svc.face_verification, "peek", boom)
    monkeypatch.setattr(svc.face_verification, "consume", boom)
    assert client.get(f"{P}/attendance/me/status", headers=h).status_code == 503
    assert client.post(f"{P}/attendance/check-in", headers=h).status_code == 503
