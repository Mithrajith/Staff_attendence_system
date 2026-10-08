import os
import re
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update(
    APP_ENV="development",
    JWT_SECRET_KEY="x" * 48,
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    EMAIL_BACKEND="console",
    INFERENCE_MOUNT_ENABLED="false",  # keeps torch out of the unit tests
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.core.security import hash_password  # noqa: E402
from api.main import app  # noqa: E402
from api.services import users as svc  # noqa: E402
from database.models import Role, User  # noqa: E402
from database.session import Base, _session_factory, get_engine  # noqa: E402

P = "/api/v1"
PW = "correct-horse-battery"


@pytest.fixture(autouse=True)
def db():
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(svc, "send_email", lambda to, subject, template, **ctx: sent.append((to, template, ctx)))
    return sent


def make_user(email, role=Role.staff, active=True):
    with _session_factory()() as s:
        u = User(email=email, full_name=email.split("@")[0], role=role, password_hash=hash_password(PW), is_active=active)
        s.add(u)
        s.commit()
        return u.id


def login(client, email, password=PW):
    r = client.post(f"{P}/auth/login", json={"username": email, "password": password})
    return r


def auth(client, email):
    r = login(client, email)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_login_failures(client):
    make_user("a@x.com")
    assert login(client, "a@x.com", "wrong-password").status_code == 401
    assert login(client, "nobody@x.com").status_code == 401
    make_user("off@x.com", active=False)
    assert login(client, "off@x.com").status_code == 403


def test_requires_auth(client):
    assert client.get(f"{P}/users/me").status_code == 401
    assert client.get(f"{P}/users/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_staff_only_sees_self(client):
    staff = make_user("s@x.com")
    other = make_user("o@x.com")
    h = auth(client, "s@x.com")
    assert client.get(f"{P}/users/me", headers=h).json()["email"] == "s@x.com"
    assert client.get(f"{P}/users/{staff}", headers=h).status_code == 200
    assert client.get(f"{P}/users/{other}", headers=h).status_code == 403
    assert client.get(f"{P}/users", headers=h).status_code == 403
    assert client.post(f"{P}/users", headers=h, json={"email": "n@x.com", "full_name": "N"}).status_code == 403
    assert client.post(f"{P}/users/{other}/deactivate", headers=h).status_code == 403
    assert client.delete(f"{P}/users/{other}", headers=h).status_code == 403
    assert client.put(f"{P}/users/{other}/password", headers=h, json={"new_password": "brand-new-password"}).status_code == 403


def test_system_role_has_no_user_permissions(client):
    other = make_user("o@x.com")
    make_user("sys@x.com", Role.system)
    h = auth(client, "sys@x.com")
    assert client.get(f"{P}/users", headers=h).status_code == 403
    assert client.get(f"{P}/users/{other}", headers=h).status_code == 403
    assert client.get(f"{P}/users/me", headers=h).status_code == 200


def test_admin_create_with_welcome_email_and_set_password(client, emails):
    make_user("admin@x.com", Role.admin)
    h = auth(client, "admin@x.com")
    r = client.post(f"{P}/users", headers=h, json={"email": "New@X.com", "full_name": "New", "role": "staff"})
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "new@x.com" and "password" not in r.text
    assert emails[0][0] == "new@x.com" and emails[0][1] == "welcome"
    token = re.search(r"token=(.+)$", emails[0][2]["link"]).group(1)
    assert client.post(f"{P}/auth/reset-password", json={"token": token, "new_password": "my-new-password"}).status_code == 200
    assert login(client, "new@x.com", "my-new-password").status_code == 200
    # single use
    assert client.post(f"{P}/auth/reset-password", json={"token": token, "new_password": "another-password"}).status_code == 400
    # duplicate
    assert client.post(f"{P}/users", headers=h, json={"email": "new@x.com", "full_name": "Dup"}).status_code == 409


def test_admin_create_with_password_sends_no_email(client, emails):
    make_user("admin@x.com", Role.admin)
    h = auth(client, "admin@x.com")
    r = client.post(f"{P}/users", headers=h, json={"email": "p@x.com", "full_name": "P", "password": "chosen-password"})
    assert r.status_code == 201 and not emails
    assert login(client, "p@x.com", "chosen-password").status_code == 200


def test_validation(client):
    make_user("admin@x.com", Role.admin)
    h = auth(client, "admin@x.com")
    assert client.post(f"{P}/users", headers=h, json={"email": "bad", "full_name": "x"}).status_code == 422
    assert client.post(f"{P}/users", headers=h, json={"email": "a@b.com", "full_name": "x", "password": "short"}).status_code == 422
    assert client.post(f"{P}/users", headers=h, json={"email": "a@b.com", "full_name": "x", "role": "root"}).status_code == 422


def test_list_filter_and_pagination(client):
    make_user("admin@x.com", Role.admin)
    for i in range(3):
        make_user(f"s{i}@x.com")
    h = auth(client, "admin@x.com")
    r = client.get(f"{P}/users", headers=h, params={"role": "staff", "limit": 2}).json()
    assert r["total"] == 3 and len(r["items"]) == 2
    assert client.get(f"{P}/users", headers=h, params={"q": "s1@"}).json()["total"] == 1
    assert client.get(f"{P}/users", headers=h, params={"limit": 1000}).status_code == 422


def test_activate_deactivate_revokes_access(client):
    admin = make_user("admin@x.com", Role.admin)
    staff = make_user("s@x.com")
    ha, hs = auth(client, "admin@x.com"), auth(client, "s@x.com")
    assert client.post(f"{P}/users/{staff}/deactivate", headers=ha).json()["is_active"] is False
    assert client.get(f"{P}/users/me", headers=hs).status_code == 401
    assert login(client, "s@x.com").status_code == 403
    assert client.post(f"{P}/users/{staff}/activate", headers=ha).json()["is_active"] is True
    assert client.get(f"{P}/users/me", headers=hs).status_code == 200
    assert client.post(f"{P}/users/{admin}/deactivate", headers=ha).status_code == 400
    assert client.post(f"{P}/users/9999/deactivate", headers=ha).status_code == 404


def test_delete(client):
    admin = make_user("admin@x.com", Role.admin)
    staff = make_user("s@x.com")
    h = auth(client, "admin@x.com")
    assert client.delete(f"{P}/users/{admin}", headers=h).status_code == 400
    assert client.delete(f"{P}/users/{staff}", headers=h).status_code == 204
    assert client.get(f"{P}/users/{staff}", headers=h).status_code == 404


def test_forgot_and_reset_password(client, emails):
    make_user("s@x.com")
    make_user("off@x.com", active=False)
    old = auth(client, "s@x.com")
    r1 = client.post(f"{P}/auth/forgot-password", json={"username": "s@x.com"})
    r2 = client.post(f"{P}/auth/forgot-password", json={"username": "ghost@x.com"})
    client.post(f"{P}/auth/forgot-password", json={"username": "off@x.com"})
    assert r1.status_code == r2.status_code == 202 and r1.json() == r2.json()
    assert [e[0] for e in emails] == ["s@x.com"]
    token = re.search(r"token=(.+)$", emails[0][2]["link"]).group(1)
    assert client.post(f"{P}/auth/reset-password", json={"token": "z" * 43, "new_password": "my-new-password"}).status_code == 400
    assert client.post(f"{P}/auth/reset-password", json={"token": token, "new_password": "my-new-password"}).status_code == 200
    assert login(client, "s@x.com").status_code == 401
    assert login(client, "s@x.com", "my-new-password").status_code == 200
    assert client.get(f"{P}/users/me", headers=old).status_code == 401  # old session revoked


def test_expired_reset_token(client, emails, monkeypatch):
    make_user("s@x.com")
    client.post(f"{P}/auth/forgot-password", json={"username": "s@x.com"})
    token = re.search(r"token=(.+)$", emails[0][2]["link"]).group(1)
    from datetime import timedelta
    from database import models

    monkeypatch.setattr(svc, "utcnow", lambda: models.utcnow() + timedelta(hours=2))
    assert client.post(f"{P}/auth/reset-password", json={"token": token, "new_password": "my-new-password"}).status_code == 400


def test_change_password_self_and_admin(client):
    staff = make_user("s@x.com")
    make_user("admin@x.com", Role.admin)
    hs, ha = auth(client, "s@x.com"), auth(client, "admin@x.com")
    url = f"{P}/users/{staff}/password"
    assert client.put(url, headers=hs, json={"new_password": "brand-new-password"}).status_code == 400
    assert client.put(url, headers=hs, json={"current_password": "nope", "new_password": "brand-new-password"}).status_code == 400
    assert client.put(url, headers=hs, json={"current_password": PW, "new_password": "brand-new-password"}).status_code == 204
    assert client.get(f"{P}/users/me", headers=hs).status_code == 401
    assert login(client, "s@x.com", "brand-new-password").status_code == 200
    assert client.put(url, headers=ha, json={"new_password": "admin-set-password"}).status_code == 204
    assert login(client, "s@x.com", "admin-set-password").status_code == 200


def test_healthz_and_docs(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_production_config_rejects_weak_settings(monkeypatch):
    from api.core.config import Settings

    monkeypatch.delenv("DATABASE_URL")
    base = dict(app_env="production", jwt_secret_key="y" * 40, mysql_password="s3cure-pw-value", email_backend="smtp", smtp_host="smtp.x.com")
    Settings(**base)
    for bad in (
        {"jwt_secret_key": "short"},
        {"jwt_secret_key": "change-me" + "y" * 40},
        {"mysql_password": None},
        {"email_backend": "console"},
        {"cors_origins": ["*"]},
    ):
        with pytest.raises(ValueError):
            Settings(**{**base, **bad})
