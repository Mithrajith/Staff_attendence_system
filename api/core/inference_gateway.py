"""Auth gate for the inference service when it is mounted inside the main API.

The inference routes are unauthenticated by themselves. Behind this gate:
  * admins can reach everything (including the sandbox, bulk ingest and the live events feed);
  * any other active user can only use the live stream and health check.
Browsers can't set headers on WebSockets, so `?token=<access token>` is accepted for WebSocket connections.
"""

from urllib.parse import parse_qs

from fastapi.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from api.core.security import decode_access_token
from api.deps import Permission, has_permission
from database.models import Role, User
from database.session import _session_factory

NON_ADMIN_PATHS = ("/health", "/ws/stream/")


def _token(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token if scheme.lower() == "bearer" and token else None
    if scope["type"] == "websocket":
        return (parse_qs(scope.get("query_string", b"").decode()).get("token") or [None])[0]
    return None


def _load_user(token: str) -> User | None:
    payload = decode_access_token(token)
    if payload is None or not str(payload["sub"]).isdigit():
        return None
    with _session_factory()() as db:
        user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active or user.token_version != payload["tv"]:
        return None
    return user


class InferenceAuth:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def _deny(self, scope: Scope, receive: Receive, send: Send, code: int, detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await JSONResponse({"detail": detail}, status_code=code)(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        token = _token(scope)
        user = await run_in_threadpool(_load_user, token) if token else None
        if user is None:
            return await self._deny(scope, receive, send, 401, "Invalid or expired credentials")

        path = scope["path"]
        root = scope.get("root_path", "")
        rel = path[len(root):] if root and path.startswith(root) else path
        if user.role != Role.admin and not (
            rel.startswith(NON_ADMIN_PATHS) and has_permission(user, Permission.attendance_mark_self)
        ):
            return await self._deny(scope, receive, send, 403, "Insufficient permissions")
        await self.app(scope, receive, send)
