import json
import logging
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update(
    APP_ENV="development",
    JWT_SECRET_KEY="x" * 48,
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    EMAIL_BACKEND="console",
    INFERENCE_MOUNT_ENABLED="false",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.core.config import get_settings  # noqa: E402
from api.core.logs import audit, redact, setup_logging  # noqa: E402
from api.core.security import hash_password  # noqa: E402
from api.main import app  # noqa: E402
from database.models import Role, User  # noqa: E402
from database.session import Base, _session_factory, get_engine  # noqa: E402

P = "/api/v1"
PW = "correct-horse-battery"


def _flush():
    for h in logging.getLogger().handlers:
        h.flush()


def _lines(path):
    _flush()
    return path.read_text(encoding="utf-8").splitlines()


@pytest.fixture(autouse=True)
def db():
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())


@pytest.fixture
def logdir(tmp_path):
    setup_logging(get_settings().model_copy(update={"log_dir": str(tmp_path), "log_format": "json", "log_level": "INFO"}))
    yield tmp_path
    setup_logging(get_settings().model_copy(update={"log_dir": "", "log_format": "text"}))


@pytest.fixture
def client():
    return TestClient(app)


def _make_user(email="a@x.com"):
    with _session_factory()() as s:
        s.add(User(email=email, full_name="A", role=Role.staff, password_hash=hash_password(PW), is_active=True))
        s.commit()


def test_redact_masks_secrets():
    assert "abc.def" not in redact("GET /ws?token=abc.def HTTP")
    assert "abc.def" not in redact("Authorization: Bearer abc.def")
    assert "hunter2" not in redact('{"password": "hunter2"}')
    assert "hunter2" not in redact("password=hunter2")
    assert redact("plain message") == "plain message"


def test_files_are_created_and_json_is_valid(logdir):
    logging.getLogger("api.test").info("hello", extra={"foo": "bar"})
    for name in ("app.log", "error.log", "access.log", "audit.log"):
        assert (logdir / name).exists()
    rec = json.loads(_lines(logdir / "app.log")[-1])
    assert rec["msg"] == "hello" and rec["foo"] == "bar" and rec["level"] == "INFO"


def test_error_log_only_has_errors_and_audit_is_separate(logdir):
    log = logging.getLogger("api.test")
    log.info("just info")
    log.error("boom")
    audit("something_happened", actor_id=1)
    errors = _lines(logdir / "error.log")
    assert len(errors) == 1 and json.loads(errors[0])["msg"] == "boom"
    audit_rec = json.loads(_lines(logdir / "audit.log")[-1])
    assert audit_rec["event"] == "something_happened" and audit_rec["actor_id"] == 1
    assert "something_happened" not in (logdir / "app.log").read_text(encoding="utf-8")


def test_setup_is_idempotent(logdir):
    setup_logging(get_settings().model_copy(update={"log_dir": str(logdir), "log_format": "json"}))
    logging.getLogger("api.test").info("once")
    assert sum("once" in line for line in _lines(logdir / "app.log")) == 1


def test_request_id_is_echoed_or_replaced(client, logdir):
    r = client.get("/healthz", headers={"X-Request-ID": "trace-123"})
    assert r.headers["X-Request-ID"] == "trace-123"
    r = client.get("/healthz", headers={"X-Request-ID": "bad id\twith spaces"})
    assert r.headers["X-Request-ID"] != "bad id\twith spaces" and len(r.headers["X-Request-ID"]) <= 64


def test_access_log_has_no_query_string(client, logdir):
    client.get("/api/v1/users/me?token=SECRETVALUE")
    text = (logdir / "access.log").read_text(encoding="utf-8")
    assert "/api/v1/users/me" in text and "SECRETVALUE" not in text
    rec = json.loads(_lines(logdir / "access.log")[-1])
    assert rec["status"] == 401 and rec["method"] == "GET" and "duration_ms" in rec


def test_login_events_are_audited_without_password(client, logdir):
    _make_user()
    client.post(f"{P}/auth/login", json={"username": "a@x.com", "password": "wrong-password"})
    client.post(f"{P}/auth/login", json={"username": "a@x.com", "password": PW})
    text = (logdir / "audit.log").read_text(encoding="utf-8")
    assert "login_failed" in text and "login_success" in text
    assert PW not in text and "wrong-password" not in text


def test_unwritable_log_dir_falls_back_to_console(tmp_path, capfd):
    blocker = tmp_path / "file"
    blocker.write_text("x")  # a file where a directory is expected cannot be created
    setup_logging(get_settings().model_copy(update={"log_dir": str(blocker / "logs"), "log_format": "text"}))
    try:
        logging.getLogger("api.test").warning("still works")
        out = capfd.readouterr().out
        assert "File logging disabled" in out and "still works" in out
    finally:
        setup_logging(get_settings().model_copy(update={"log_dir": "", "log_format": "text"}))
