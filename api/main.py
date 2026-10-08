import os
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.core.config import get_settings
from api.routes import web
from api.routes.v1.router import router as v1_router
from database.session import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()  # fail fast on bad configuration
    async with AsyncExitStack() as stack:
        inference_app = getattr(app.state, "inference_app", None)
        if inference_app is not None:
            # Mounted apps don't get their own lifespan; run it so the models are warm at startup.
            await stack.enter_async_context(inference_app.router.lifespan_context(inference_app))
        yield


def create_app() -> FastAPI:
    s = get_settings()
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

    if s.inference_mount_enabled:
        # Imported lazily: pulls in torch/ultralytics. The flag makes inference defer CORS to this app.
        os.environ.setdefault("INFERENCE_EMBEDDED", "true")
        from api.core.inference_gateway import InferenceAuth
        from inference.api import app as inference_app

        app.state.inference_app = inference_app
        app.mount(f"{s.api_prefix}/inference", InferenceAuth(inference_app))
    return app


app = create_app()
