from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.core.config import get_settings
from api.routes.v1.router import router as v1_router
from database.session import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()  # fail fast on bad configuration
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
    return app


app = create_app()
