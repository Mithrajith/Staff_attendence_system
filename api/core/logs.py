"""Application logging: console + rotating files, request ids, an access log and an audit trail.

Files under LOG_DIR (each size-rotated):
  app.log     everything except access/audit lines
  error.log   ERROR and above
  access.log  one line per HTTP request (path only, never the query string)
  audit.log   security-relevant events: logins, user changes, enrollment, attendance records

Secrets that slip into a message (bearer tokens, `token=` query values, password fields) are masked before
anything is written. The dev-only console email backend is exempt so reset links stay clickable in development.
"""

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter

from starlette.datastructures import MutableHeaders

from api.core.config import Settings, get_settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
client_ip_var: ContextVar[str] = ContextVar("client_ip", default="-")

access_logger = logging.getLogger("api.access")
audit_logger = logging.getLogger("api.audit")
logger = logging.getLogger("api.logs")

_HANDLER_MARK = "_staff_attendance_handler"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_REDACT = [
    (re.compile(r"(?i)(token=)[^\s&\"']+"), r"\1***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (re.compile(r"(?i)(\"?(?:password|new_password|current_password|secret|authorization)\"?\s*[:=]\s*\"?)[^\s,\"}&]+"), r"\1***"),
]
_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName", "request_id", "color_message"}
_UNMASKED_LOGGERS = {"api.email"}


def redact(text: str) -> str:
    for pattern, repl in _REDACT:
        text = pattern.sub(repl, text)
    return text


def _extras(record: logging.LogRecord) -> dict:
    return {k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS and not k.startswith("_")}


class _Base(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = self.render(record)
        return out if record.name in _UNMASKED_LOGGERS else redact(out)

    def render(self, record: logging.LogRecord) -> str:
        raise NotImplementedError


class TextFormatter(_Base):
    def render(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} {record.levelname:<7} {record.name} [{getattr(record, 'request_id', '-')}] {record.getMessage()}"
        extras = " ".join(f"{k}={v}" for k, v in _extras(record).items())
        if extras:
            line += " " + extras
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(_Base):
    def render(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        data.update(_extras(record))
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str, ensure_ascii=False)


def _install_record_factory() -> None:
    current = logging.getLogRecordFactory()
    if getattr(current, "_staff_attendance", False):
        return

    def factory(*args, **kwargs):
        record = current(*args, **kwargs)
        record.request_id = request_id_var.get()
        return record

    factory._staff_attendance = True
    logging.setLogRecordFactory(factory)


def setup_logging(settings: Settings | None = None) -> None:
    """Idempotent: replaces handlers installed by an earlier call and leaves foreign handlers alone."""
    s = settings or get_settings()
    _install_record_factory()
    fmt = s.log_format if s.log_format != "auto" else ("json" if s.is_production else "text")
    formatter = JsonFormatter() if fmt == "json" else TextFormatter()

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _HANDLER_MARK, False):
            root.removeHandler(h)
            h.close()
    root.setLevel(s.log_level)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    problem = None
    if s.log_dir:
        try:
            directory = Path(s.log_dir)
            directory.mkdir(parents=True, exist_ok=True)

            def rotating(name: str, level: int = logging.NOTSET, only=None, skip=None) -> None:
                h = RotatingFileHandler(
                    directory / name, maxBytes=s.log_max_bytes, backupCount=s.log_backup_count, encoding="utf-8"
                )
                h.setLevel(level)
                if only:
                    h.addFilter(lambda r: r.name in only)
                if skip:
                    h.addFilter(lambda r: r.name not in skip)
                handlers.append(h)

            rotating("app.log", skip={"api.access", "api.audit"})
            rotating("error.log", logging.ERROR)
            rotating("access.log", only={"api.access"})
            rotating("audit.log", only={"api.audit"})
        except OSError as exc:  # keep serving, but make the missing files impossible to miss
            problem = exc

    for h in handlers:
        h.setFormatter(formatter)
        setattr(h, _HANDLER_MARK, True)
        root.addHandler(h)

    # uvicorn installs its own handlers; route them through ours and drop its access log (we log requests).
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    lg = logging.getLogger("uvicorn.access")
    lg.handlers.clear()
    lg.propagate = False
    for name in ("httpx", "httpcore", "urllib3", "multipart", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)

    if problem:
        logger.error("File logging disabled: cannot write to LOG_DIR=%s (%s)", s.log_dir, problem)


def audit(event: str, **fields) -> None:
    """Record a security-relevant event. Never pass secrets; ids and usernames only."""
    audit_logger.info(event, extra={"event": event, "ip": client_ip_var.get(), **fields})


class RequestLogMiddleware:
    """Pure ASGI (no response buffering, safe for websockets). Assigns/echoes X-Request-ID and writes the access log."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        client = scope.get("client")
        rid_token = request_id_var.set(rid)
        ip_token = client_ip_var.set(client[0] if client else "-")
        status = 500  # stays 500 if the app raises; Starlette then answers with its own 500
        start = perf_counter()

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope["path"]
            level = logging.DEBUG if path == "/healthz" else logging.WARNING if status >= 500 else logging.INFO
            access_logger.log(
                level,
                "%s %s %s",
                scope["method"],
                path,
                status,
                extra={
                    "method": scope["method"],
                    "path": path,
                    "status": status,
                    "duration_ms": round((perf_counter() - start) * 1000, 1),
                    "ip": client_ip_var.get(),
                },
            )
            request_id_var.reset(rid_token)
            client_ip_var.reset(ip_token)
