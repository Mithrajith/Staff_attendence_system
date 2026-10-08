import asyncio

import numpy as np
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient as StarletteClient

from api.core.config import get_settings
from api.core.inference_gateway import InferenceAuth
from api.services import face as face_svc
from database.models import Role, User
from database.session import _session_factory
from test_user_api import P, PW, auth, client, db, login, make_user  # noqa: F401


def add_department(client, name="Engineering", code="001"):
    make_user("admin@x.com", Role.admin)
    r = client.post(f"{P}/departments", headers=auth(client, "admin@x.com"), json={"code": code, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def signup_body(dept, **over):
    body = {"username": "Alice", "employee_id": "E100", "department_id": dept, "email": "alice@x.com", "password": PW}
    return {**body, **over}


# --- departments ---------------------------------------------------------------------------------------------


def test_departments_admin_only_writes_public_read(client):
    dept = add_department(client)
    admin = auth(client, "admin@x.com")
    make_user("s@x.com")
    staff = auth(client, "s@x.com")

    assert [d["name"] for d in client.get(f"{P}/departments").json()] == ["Engineering"]
    assert client.post(f"{P}/departments", json={"code": "9", "name": "HR"}).status_code == 401
    assert client.post(f"{P}/departments", headers=staff, json={"code": "9", "name": "HR"}).status_code == 403
    assert client.put(f"{P}/departments/{dept}", headers=staff, json={"code": "1", "name": "X"}).status_code == 403
    assert client.delete(f"{P}/departments/{dept}", headers=staff).status_code == 403

    assert client.post(f"{P}/departments", headers=admin, json={"code": "5", "name": "Engineering"}).status_code == 409
    assert client.put(f"{P}/departments/{dept}", headers=admin, json={"code": "001", "name": "Eng"}).json()["name"] == "Eng"
    assert client.post(f"{P}/departments", headers=admin, json={"code": "001", "name": "Other"}).status_code == 409
    assert client.put(f"{P}/departments/999", headers=admin, json={"code": "001", "name": "Eng2"}).status_code == 404


def test_department_in_use_cannot_be_deleted(client):
    dept = add_department(client)
    assert client.post(f"{P}/auth/signup", json=signup_body(dept)).status_code == 201
    admin = auth(client, "admin@x.com")
    assert client.delete(f"{P}/departments/{dept}", headers=admin).status_code == 409
    other = client.post(f"{P}/departments", headers=admin, json={"code": "7", "name": "Empty"}).json()["id"]
    assert client.delete(f"{P}/departments/{other}", headers=admin).status_code == 204


# --- signup / login ------------------------------------------------------------------------------------------


def test_signup_then_login_by_username_or_email(client):
    dept = add_department(client)
    r = client.post(f"{P}/auth/signup", json=signup_body(dept))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["username"] == "alice" and body["user"]["role"] == "staff"
    assert body["user"]["department"]["name"] == "Engineering" and body["user"]["face_enrolled"] is False
    assert "password" not in r.text

    me = client.get(f"{P}/users/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert login(client, "ALICE").status_code == 200
    assert client.post(f"{P}/auth/login", json={"username": "ALICE", "password": PW}).status_code == 200
    assert client.post(f"{P}/auth/login", json={"username": "alice@x.com", "password": PW}).status_code == 200


def test_signup_rejects_duplicates_and_bad_input(client):
    dept = add_department(client)
    assert client.post(f"{P}/auth/signup", json=signup_body(dept)).status_code == 201
    assert client.post(f"{P}/auth/signup", json=signup_body(dept, username="alice", email="b@x.com", employee_id="E2")).status_code == 409
    assert client.post(f"{P}/auth/signup", json=signup_body(dept, username="bob", email="alice@x.com", employee_id="E2")).status_code == 409
    assert client.post(f"{P}/auth/signup", json=signup_body(dept, username="bob", email="b@x.com")).status_code == 409
    assert client.post(f"{P}/auth/signup", json=signup_body(999, username="bob", email="b@x.com", employee_id="E2")).status_code == 422
    assert client.post(f"{P}/auth/signup", json=signup_body(dept, username="b!", email="b@x.com", employee_id="E2")).status_code == 422
    assert client.post(f"{P}/auth/signup", json=signup_body(dept, password="short")).status_code == 422


def test_signup_can_be_disabled(client, monkeypatch):
    dept = add_department(client)
    monkeypatch.setattr(get_settings(), "signup_enabled", False)
    assert client.post(f"{P}/auth/signup", json=signup_body(dept)).status_code == 403


def test_signup_ignores_role_field(client):
    dept = add_department(client)
    r = client.post(f"{P}/auth/signup", json=signup_body(dept, role="admin"))
    assert r.status_code == 201 and r.json()["user"]["role"] == "staff"


# --- face enrollment -----------------------------------------------------------------------------------------

IMG = "A" * 200


@pytest.fixture
def inference(monkeypatch):
    """Stubs the inference seams. `state` lets tests tweak behaviour."""
    state = {"vectors": 0, "faces": True, "identity": "Unknown", "stored": 5, "registered": [], "deleted": []}
    monkeypatch.setattr(face_svc, "vector_count", lambda uid: state["vectors"])
    monkeypatch.setattr(face_svc, "extract_single_face", lambda img: np.zeros((100, 100, 3), np.uint8) if state["faces"] else None)
    monkeypatch.setattr(face_svc, "identify_faces", lambda crops: [state["identity"]] * len(crops))
    monkeypatch.setattr(face_svc, "delete_vectors", lambda uid: state["deleted"].append(uid))

    async def register(uid, images):
        state["registered"].append((uid, len(images)))
        return state["stored"]

    monkeypatch.setattr(face_svc, "register", register)
    return state


def signed_up(client):
    dept = add_department(client)
    r = client.post(f"{P}/auth/signup", json=signup_body(dept)).json()
    return r["user"]["id"], {"Authorization": f"Bearer {r['access_token']}"}


def enrolled_flag(client, h):
    return client.get(f"{P}/users/me", headers=h).json()["face_enrolled"]


def test_enroll_success(client, inference):
    uid, h = signed_up(client)
    r = client.post(f"{P}/face/enroll", headers=h, json={"images": [IMG] * 4})
    assert r.status_code == 201, r.text
    assert r.json() == {"enrolled": True, "embeddings_stored": 5}
    assert inference["registered"] == [(uid, 4)]
    assert enrolled_flag(client, h) is True
    assert client.get(f"{P}/face/enrollment", headers=h).json()["enrolled"] is True


def test_enroll_twice_is_conflict(client, inference):
    _, h = signed_up(client)
    assert client.post(f"{P}/face/enroll", headers=h, json={"images": [IMG] * 4}).status_code == 201
    assert client.post(f"{P}/face/enroll", headers=h, json={"images": [IMG] * 4}).status_code == 409
    assert len(inference["registered"]) == 1


def test_enroll_blocked_when_vector_db_already_has_user(client, inference):
    _, h = signed_up(client)
    inference["vectors"] = 3
    assert client.post(f"{P}/face/enroll", headers=h, json={"images": [IMG] * 4}).status_code == 409
    assert inference["registered"] == [] and enrolled_flag(client, h) is False


def test_enroll_blocked_when_face_belongs_to_someone_else(client, inference):
    _, h = signed_up(client)
    inference["identity"] = "9999"
    r = client.post(f"{P}/face/enroll", headers=h, json={"images": [IMG] * 4})
    assert r.status_code == 409 and "another account" in r.json()["detail"]
    assert inference["registered"] == []


def test_enroll_needs_enough_clear_faces(client, inference):
    _, h = signed_up(client)
    inference["faces"] = False
    assert client.post(f"{P}/face/enroll", headers=h, json={"images": [IMG] * 4}).status_code == 422
    inference["faces"] = True
    assert client.post(f"{P}/face/enroll", headers=h, json={"images": [IMG]}).status_code == 422
    assert client.post(f"{P}/face/enroll", headers=h, json={"images": [IMG] * 11}).status_code == 422
    assert inference["registered"] == []


def test_enroll_failure_cleans_up_vectors(client, inference):
    uid, h = signed_up(client)
    inference["stored"] = 1
    assert client.post(f"{P}/face/enroll", headers=h, json={"images": [IMG] * 4}).status_code == 422
    assert inference["deleted"] == [uid] and enrolled_flag(client, h) is False


def test_enroll_vector_db_down_is_503(client, inference, monkeypatch):
    _, h = signed_up(client)

    def boom(uid):
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(face_svc, "vector_count", boom)
    assert client.post(f"{P}/face/enroll", headers=h, json={"images": [IMG] * 4}).status_code == 503


def test_enroll_requires_auth_and_permission(client, inference):
    assert client.post(f"{P}/face/enroll", json={"images": [IMG] * 4}).status_code == 401
    make_user("sys@x.com", Role.system)
    assert client.post(f"{P}/face/enroll", headers=auth(client, "sys@x.com"), json={"images": [IMG] * 4}).status_code == 403


# --- web page ------------------------------------------------------------------------------------------------


def test_web_pages(client):
    for path in ("/", "/reset-password?token=abc"):
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert 'const API = "/api/v1"' in r.text
        assert r.headers["x-frame-options"] == "DENY"


# --- inference gateway ---------------------------------------------------------------------------------------


def _stub():
    async def ok(request):
        return PlainTextResponse("ok")

    async def ws(socket):
        await socket.accept()
        await socket.send_text("hi")
        await socket.close()

    return StarletteClient(
        InferenceAuth(Starlette(routes=[Route("/health", ok), Route("/testing/x", ok), WebSocketRoute("/ws/stream/1", ws), WebSocketRoute("/ws/events", ws)]))
    )


def test_gateway_http_access_rules(client):
    make_user("a@x.com", Role.admin)
    make_user("s@x.com")
    make_user("sys@x.com", Role.system)
    gate = _stub()
    bearer = lambda e: {"Authorization": auth(client, e)["Authorization"]}  # noqa: E731

    assert gate.get("/health").status_code == 401
    assert gate.get("/health", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert gate.get("/health", headers=bearer("s@x.com")).status_code == 200
    assert gate.get("/testing/x", headers=bearer("s@x.com")).status_code == 403
    assert gate.get("/testing/x", headers=bearer("sys@x.com")).status_code == 403
    assert gate.get("/testing/x", headers=bearer("a@x.com")).status_code == 200


def test_gateway_revoked_when_deactivated(client):
    uid = make_user("s@x.com")
    h = {"Authorization": auth(client, "s@x.com")["Authorization"]}
    gate = _stub()
    assert gate.get("/health", headers=h).status_code == 200
    with _session_factory()() as s:
        s.get(User, uid).is_active = False
        s.commit()
    assert gate.get("/health", headers=h).status_code == 401


def test_gateway_websocket(client):
    make_user("a@x.com", Role.admin)
    make_user("s@x.com")
    gate = _stub()
    tok = lambda e: auth(client, e)["Authorization"].split()[1]  # noqa: E731

    with gate.websocket_connect(f"/ws/stream/1?token={tok('s@x.com')}") as ws:
        assert ws.receive_text() == "hi"
    with pytest.raises(Exception):
        with gate.websocket_connect("/ws/stream/1"):
            pass
    with pytest.raises(Exception):
        with gate.websocket_connect(f"/ws/events?token={tok('s@x.com')}"):
            pass
    with gate.websocket_connect(f"/ws/events?token={tok('a@x.com')}") as ws:
        assert ws.receive_text() == "hi"
