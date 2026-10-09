import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

from api.core.config import get_settings
from api.core.logs import RequestLogMiddleware, setup_logging
from api.routes import web
from api.routes.v1.router import router as v1_router
from database.session import get_engine

logger = logging.getLogger("api.main")


class SecurityHeadersMiddleware:
    """Applies production security headers across all HTTP responses."""

    def __init__(self, app: ASGIApp, is_production: bool = False):
        self.app = app
        self.is_production = is_production

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-XSS-Protection"] = "1; mode=block"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                if self.is_production:
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            await send(message)

        await self.app(scope, receive, send_wrapper)


class CachedStaticFiles(StaticFiles):
    """StaticFiles with Cache-Control headers for production performance."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=86400, immutable"
        return response



@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()  # fail fast on bad configuration
    logger.info("Starting (env=%s, log_dir=%s)", s.app_env, s.log_dir or "console only")
    async with AsyncExitStack() as stack:
        inference_app = getattr(app.state, "inference_app", None)
        if inference_app is not None:
            # Mounted apps don't get their own lifespan; run it so the models are warm at startup.
            await stack.enter_async_context(inference_app.router.lifespan_context(inference_app))
        yield
    logger.info("Stopped")


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(s)  # before the inference import, whose module calls logging.basicConfig
    app = FastAPI(
        title="Staff Attendance API",
        lifespan=lifespan,
        # Interactive docs expose the full API surface; keep them off in production.
        docs_url=None if s.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if s.is_production else "/openapi.json",
    )
    if s.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.cors_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.add_middleware(SecurityHeadersMiddleware, is_production=s.is_production)
    app.add_middleware(RequestLogMiddleware)  # added last = outermost, so it sees every response

    @app.get("/healthz", include_in_schema=False)
    def healthz(response: Response):
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            response.status_code = 503
            return {"status": "unavailable"}
        return {"status": "ok"}

    app.include_router(v1_router, prefix=s.api_prefix)
    app.include_router(web.router)

    static_dir = Path(__file__).resolve().parent / "static"
    if not static_dir.exists():
        static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", CachedStaticFiles(directory=static_dir), name="static")


    if s.inference_mount_enabled:
        # Imported lazily: pulls in torch/ultralytics. The flag makes inference defer CORS to this app.
        os.environ.setdefault("INFERENCE_EMBEDDED", "true")
        from api.core.inference_gateway import InferenceAuth
        from inference.api import app as inference_app

        app.state.inference_app = inference_app
        app.mount(f"{s.api_prefix}/inference", InferenceAuth(inference_app))
    return app


app = create_app()
