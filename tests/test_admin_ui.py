from test_attendance_api import faces  # noqa: F401
from test_user_api import P, auth, client, db, make_user  # noqa: F401

from api.services import attendance as svc  # noqa: F401  (patched through the faces fixture)
from database.models import Role


def test_admin_pages_and_summary_are_admin_only(client):
    make_user("a@x.com", Role.admin)
    make_user("s@x.com")
    make_user("sys@x.com", Role.system)

    page = client.get("/admin")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"] and page.headers["x-frame-options"] == "DENY"

    assert client.get(f"{P}/admin/summary").status_code == 401
    assert client.get(f"{P}/admin/summary", headers=auth(client, "s@x.com")).status_code == 403
    assert client.get(f"{P}/admin/summary", headers=auth(client, "sys@x.com")).status_code == 403
    assert client.get(f"{P}/admin/summary", headers=auth(client, "a@x.com")).status_code == 200


def test_summary_counts_and_recent_activity(client, faces):
    make_user("a@x.com", Role.admin)
    in_now = make_user("in@x.com")
    done = make_user("done@x.com")
    make_user("off@x.com", active=False)

    for uid, email, out in ((in_now, "in@x.com", False), (done, "done@x.com", True)):
        h = auth(client, email)
        faces.add(uid)
        assert client.post(f"{P}/attendance/check-in", headers=h).status_code == 201
        if out:
            faces.add(uid)
            assert client.post(f"{P}/attendance/check-out", headers=h).status_code == 201

    admin = auth(client, "a@x.com")
    s = client.get(f"{P}/admin/summary", headers=admin).json()
    assert s == {
        "users_total": 4, "users_active": 3, "users_face_enrolled": 0, "departments": 0,
        "checked_in_today": 2, "currently_in": 1,
    }

    events = client.get(f"{P}/attendance/events", headers=admin).json()["items"]
    assert len(events) == 3
    assert events[0]["full_name"] == "done" and events[0]["event_type"] == "check_out"
    assert all("employee_id" in e for e in events)
    day = client.get(f"{P}/attendance/records", headers=admin).json()["items"][0]
    assert "employee_id" in day


def test_user_search_matches_username_and_employee_id(client):
    admin = make_user("a@x.com", Role.admin)
    h = auth(client, "a@x.com")
    r = client.post(f"{P}/users", headers=h, json={"email": "z@x.com", "full_name": "Zed", "username": "zed.q", "employee_id": "EMP777", "password": "chosen-password"})
    assert r.status_code == 201, r.text
    for q in ("zed.q", "EMP777"):
        items = client.get(f"{P}/users", headers=h, params={"q": q}).json()["items"]
        assert [u["email"] for u in items] == ["z@x.com"]
    assert admin


def test_about_page_and_static_assets(client):
    r = client.get("/about")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Faculty Attendance System" in r.text
    assert "Mithrajith K S" in r.text
    assert "Sai Dhinakar S" in r.text
    assert "Siddharth T" in r.text
    assert "Sri Shakthi Institute of Engineering" in r.text

    img = client.get("/static/img/image.png")
    assert img.status_code == 200

