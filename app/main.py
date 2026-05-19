"""FastAPI application entry point.

Run with:
    python -m app.main
or:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.config import get_settings
from app.db import get_engine
from app.logging_config import configure_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    """Application lifespan: startup and shutdown hooks."""
    settings = get_settings()
    settings.ensure_data_dir()

    from app.db import get_session_factory
    from app.services.migrations import run_migrations
    from app.services.seed_data import seed_database

    run_migrations()

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        seed_database(db, settings)

    engine = get_engine()
    log.info(
        "app_startup",
        extra={
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "data_dir": str(settings.data_dir),
            "database_url": settings.database_url,
        },
    )

    yield

    engine.dispose()
    log.info("app_shutdown")


def create_app() -> FastAPI:
    """Build and return the FastAPI application instance."""
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Lightweight HR Source of Truth for Saviynt Identity Cloud "
            "POC and integration testing."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.get_or_create_session_secret(),
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=False,
        session_cookie="hrsot_session",
    )

    # --- Routers ---
    from app.api.v1.api_keys import router as api_keys_router
    from app.api.v1.countries import router as countries_router
    from app.api.v1.departments import router as departments_router
    from app.api.v1.employees import router as employees_router
    from app.api.v1.employment_statuses import router as employment_statuses_router
    from app.api.v1.job_titles import router as job_titles_router
    from app.api.v1.oauth_clients import router as oauth_clients_router
    from app.api.v1.oauth_token import router as oauth_token_router
    from app.api.v1.session_auth import router as session_auth_router
    from app.api.v1.states_provinces import router as states_provinces_router

    # /api/v1/auth/* (session login, API keys, OAuth client management)
    app.include_router(session_auth_router, prefix="/api/v1")
    app.include_router(api_keys_router, prefix="/api/v1")
    app.include_router(oauth_clients_router, prefix="/api/v1")

    # /api/v1/* (lookup tables and employees)
    app.include_router(countries_router, prefix="/api/v1")
    app.include_router(states_provinces_router, prefix="/api/v1")
    app.include_router(employment_statuses_router, prefix="/api/v1")
    app.include_router(departments_router, prefix="/api/v1")
    app.include_router(job_titles_router, prefix="/api/v1")
    app.include_router(employees_router, prefix="/api/v1")

    # /oauth/token (RFC 6749 - mounted at root, not under /api/v1)
    app.include_router(oauth_token_router)

    # --- Meta endpoints ---

    @app.get("/health", tags=["meta"])
    async def health() -> JSONResponse:
        """Liveness probe. Returns 200 if the app is up and DB is reachable."""
        db_status = "ok"
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            log.warning("health_db_check_failed", extra={"error": str(exc)})
            db_status = "error"

        status_code = 200 if db_status == "ok" else 503
        return JSONResponse(
            status_code=status_code,
            content={
                "status": "ok" if db_status == "ok" else "degraded",
                "database": db_status,
                "version": __version__,
            },
        )

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        """Root index — points to docs."""
        return {
            "app": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()


def main() -> None:
    """Module entry point for `python -m app.main`."""
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
