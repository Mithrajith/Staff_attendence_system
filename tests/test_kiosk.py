import numpy as np
import pytest
from test_user_api import P, PW, auth, client, db, login, make_user  # noqa: F401

from api.core.config import get_settings
from api.services import face as face_svc
from database.models import Department, Role, User
from database.session import _session_factory

IMG = "A" * 200


@pytest.fixture
def cam(monkeypatch):
    """Stubs the inference seams. `state` decides what the camera 'sees'."""
    state = {"faces": 1, "face_ok": True, "identity": "Unknown", "confidence": 0.9, "boom": False}

    def count(image, ratio):
        if state["boom"]:
            raise RuntimeError("model down")
        return state["faces"]

    def extract(image):
        if state["boom"]:
            raise RuntimeError("model down")
        return np.zeros((100, 100, 3), np.uint8) if state["face_ok"] else None

    monkeypatch.setattr(face_svc, "count_faces", count)
    monkeypatch.setattr(face_svc, "extract_single_face", extract)
    monkeypatch.setattr(face_svc, "identify_scored", lambda crops: [(state["identity"], state["confidence"])])
    return state


def staff_with_department(email="s@x.com"):
    with _session_factory()() as s:
        dept = Department(code="247", name="AIML")
        s.add(dept)
        s.commit()
        dept_id = dept.id
    uid = make_user(email)
    with _session_factory()() as s:
        s.get(User, uid).department_id = dept_id
        s.commit()
    return uid


def identify(client, h):
    r = client.post(f"{P}/kiosk/identify", headers=h, json={"image": IMG})
    assert r.status_code == 200, r.text
    return r.json()


def record(client, h, token, action="check_in"):
    r = client.post(f"{P}/kiosk/record", headers=h, json={"match_token": token, "action": action})
    assert r.status_code == 200, r.text
    return r.json()


def scan(client, h, action="check_in"):
    """The whole kiosk flow: recognize the face, then press the button."""
    match = identify(client, h)
    assert match["status"] == "matched", match
    return record(client, h, match["match_token"], action)


def test_kiosk_access_rules(client, cam):
    make_user("kiosk@x.com", Role.system)
    make_user("s@x.com")
    make_user("a@x.com", Role.admin)
    bodies = {
        "detect": {"image": IMG},
        "identify": {"image": IMG},
        "record": {"match_token": "t" * 30, "action": "check_in"},
    }
    for path, body in bodies.items():
        assert client.post(f"{P}/kiosk/{path}", json=body).status_code == 401
        assert client.post(f"{P}/kiosk/{path}", headers=auth(client, "s@x.com"), json=body).status_code == 403
    assert client.post(f"{P}/kiosk/detect", headers=auth(client, "kiosk@x.com"), json=bodies["detect"]).json() == {"faces": 1}
    assert client.post(f"{P}/kiosk/detect", headers=auth(client, "a@x.com"), json=bodies["detect"]).status_code == 200
    assert client.post(f"{P}/kiosk/detect", headers=auth(client, "kiosk@x.com"), json={"image": "short"}).status_code == 422


def test_system_user_still_has_no_attendance_or_user_access(client, cam):
    make_user("kiosk@x.com", Role.system)
    h = auth(client, "kiosk@x.com")
    assert client.get(f"{P}/users", headers=h).status_code == 403
    assert client.get(f"{P}/attendance/records", headers=h).status_code == 403
    assert client.post(f"{P}/attendance/check-in", headers=h).status_code == 403


def test_kiosk_token_lives_longer(client):
    make_user("kiosk@x.com", Role.system)
    make_user("s@x.com")
    kiosk = client.post(f"{P}/auth/login", json={"username": "kiosk@x.com", "password": PW}).json()
    staff = client.post(f"{P}/auth/login", json={"username": "s@x.com", "password": PW}).json()
    assert kiosk["expires_in"] == get_settings().kiosk_token_expire_minutes * 60
    assert staff["expires_in"] == get_settings().access_token_expire_minutes * 60


def test_identify_records_nothing_until_a_button_is_pressed(client, cam):
    uid = staff_with_department()
    make_user("kiosk@x.com", Role.system)
    make_user("a@x.com", Role.admin)
    h = auth(client, "kiosk@x.com")
    cam["identity"] = str(uid)

    m = identify(client, h)  # then the person closes the window with X: no /record call
    assert m["status"] == "matched" and m["name"] == "s" and m["department"] == "AIML"
    assert m["checked_in"] is False and m["match_token"]
    assert client.get(f"{P}/attendance/events", headers=auth(client, "a@x.com")).json()["total"] == 0


def test_record_the_chosen_action_in_sequence(client, cam):
    uid = staff_with_department()
    make_user("kiosk@x.com", Role.system)
    make_user("a@x.com", Role.admin)
    h = auth(client, "kiosk@x.com")
    cam["identity"] = str(uid)

    out_first = scan(client, h, "check_out")  # can't leave before arriving
    assert out_first["status"] == "not_checked_in" and out_first["name"] == "s"

    first = scan(client, h, "check_in")
    assert first["status"] == "recorded" and first["action"] == "check_in"
    assert first["name"] == "s" and first["department"] == "AIML" and 0 <= first["hour"] < 24
    assert identify(client, h)["checked_in"] is True

    assert scan(client, h, "check_in")["status"] == "already_checked_in"
    assert scan(client, h, "check_out")["status"] == "recorded"
    assert scan(client, h, "check_out")["status"] == "not_checked_in"
    assert scan(client, h, "check_in")["status"] == "recorded"  # back again later the same day

    events = client.get(f"{P}/attendance/events", headers=auth(client, "a@x.com")).json()
    assert events["total"] == 3 and {e["user_id"] for e in events["items"]} == {uid}
    assert events["items"][0]["face_confidence"] == pytest.approx(0.9)


def test_record_rejects_bad_tokens_and_actions(client, cam):
    uid = staff_with_department()
    make_user("kiosk@x.com", Role.system)
    make_user("kiosk2@x.com", Role.system)
    make_user("a@x.com", Role.admin)
    h, h2 = auth(client, "kiosk@x.com"), auth(client, "kiosk2@x.com")
    cam["identity"] = str(uid)
    token = identify(client, h)["match_token"]

    assert record(client, h, "not-a-token-" * 3)["status"] == "expired"
    assert record(client, h2, token)["status"] == "expired"  # another kiosk can't use it
    login_token = h["Authorization"].split()[1]
    assert record(client, h, login_token)["status"] == "expired"  # nor can a login token be passed off
    assert client.post(f"{P}/kiosk/record", headers=h, json={"match_token": token, "action": "toggle"}).status_code == 422
    # and a match token is useless as a login token
    assert client.get(f"{P}/users/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    assert client.get(f"{P}/attendance/events", headers=auth(client, "a@x.com")).json()["total"] == 0
    assert record(client, h, token)["status"] == "recorded"


def test_record_expires_when_the_match_is_old_or_account_disabled(client, cam, monkeypatch):
    uid = staff_with_department()
    make_user("kiosk@x.com", Role.system)
    h = auth(client, "kiosk@x.com")
    cam["identity"] = str(uid)

    old = identify(client, h)["match_token"]
    monkeypatch.setattr("api.core.security.MATCH_TOKEN_SECONDS", -1)
    stale = identify(client, h)["match_token"]
    monkeypatch.undo()
    assert record(client, h, stale)["status"] == "expired"

    with _session_factory()() as s:  # deactivated between identify and record
        s.get(User, uid).is_active = False
        s.commit()
    assert record(client, h, old)["status"] == "expired"


def test_identify_rejects_unknown_inactive_and_non_staff(client, cam):
    make_user("kiosk@x.com", Role.system)
    make_user("a@x.com", Role.admin)
    other_system = make_user("sys2@x.com", Role.system)
    off = make_user("off@x.com", active=False)
    h = auth(client, "kiosk@x.com")

    assert identify(client, h)["status"] == "unknown"  # nobody matched
    for identity in (str(off), str(other_system), "9999", "legacy-emp-7"):
        cam["identity"] = identity
        r = identify(client, h)
        assert r["status"] == "unknown" and r["name"] is None and r["match_token"] is None and r["contact"] == "a"

    cam["face_ok"] = False
    assert identify(client, h)["status"] == "no_face"


def test_unknown_face_without_an_active_admin_has_no_contact(client, cam):
    make_user("kiosk@x.com", Role.system)
    make_user("a@x.com", Role.admin, active=False)
    r = identify(client, auth(client, "kiosk@x.com"))
    assert r["status"] == "unknown" and r["contact"] is None


def test_inference_failure_is_503(client, cam):
    make_user("kiosk@x.com", Role.system)
    h = auth(client, "kiosk@x.com")
    cam["boom"] = True
    assert client.post(f"{P}/kiosk/detect", headers=h, json={"image": IMG}).status_code == 503
    assert client.post(f"{P}/kiosk/identify", headers=h, json={"image": IMG}).status_code == 503


def test_kiosk_page(client):
    r = client.get("/kiosk")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert 'const API = "/api/v1"' in r.text and r.headers["permissions-policy"] == "camera=(self)"
