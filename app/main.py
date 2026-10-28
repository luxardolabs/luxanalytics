"""The FastAPI application: middleware, exception handlers, routers, lifespan."""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from redis.exceptions import RedisError
from slowapi.errors import RateLimitExceeded
from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.sessions import SessionMiddleware

from alembic import command
from app.api.v1.routers import router as api_router
from app.core.auth import get_session_secret
from app.core.config import settings
from app.core.limiter import limiter, rate_limit_exceeded_handler
from app.core.logging_config import configure_logging
from app.core.middleware import (
    LoggingMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    app_rate_limit_exceeded_handler,
)
from app.core.rate_limiter import AppRateLimitExceeded
from app.core.redis_client import close_redis_client
from app.core.same_origin import SameOriginMiddleware
from app.core.telemetry import setup_telemetry
from app.core.trusted_host import AllowedHostsMiddleware
from app.crud.dashboard_session_crud import ensure_session_generation
from app.db.database import async_engine

# Every model registered on Base.metadata before anything migrates or queries.
from app.models import App, Device, Event  # noqa: F401
from app.schemas.event_schema import INGEST_BODY_COMPONENTS
from app.utils.exception_handlers import general_exception_handler
from app.web.routers import router as web_router
from app.web.templates import error_templates

# Setup logging
configure_logging(
    service="luxanalytics",
    version=settings.APP_VERSION,
    environment=settings.ENVIRONMENT,
    # uvicorn's access line logs every path and query string (end-user ids, searches); the
    # LoggingMiddleware line, by route template, replaces it.
    levels={"uvicorn.access": "WARNING"},
)
logger = logging.getLogger(__name__)


async def run_migrations() -> None:
    """Run database migrations via Alembic."""
    try:
        logger.info("Running database migrations via Alembic...")

        alembic_cfg = Config("alembic.ini")

        def _run_upgrade(connection: Connection) -> None:
            alembic_cfg.attributes["connection"] = connection
            command.upgrade(alembic_cfg, "head")

        async with async_engine.begin() as conn:
            await conn.run_sync(_run_upgrade)

        logger.info("Database migrations completed successfully!")

    except Exception:
        # Refuse to serve on a schema the code does not match.
        logger.exception("Database migrations failed!")
        sys.exit(1)


async def wait_for_database() -> None:
    """Wait for database to be ready."""
    logger.info("⏳ Waiting for database to be ready...")

    max_retries = 30
    retry_count = 0

    while retry_count < max_retries:
        try:
            # Use SQLAlchemy to test the connection properly
            async with async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("✅ Database is ready!")
            return
        except (SQLAlchemyError, OSError) as e:
            retry_count += 1
            logger.info(
                "Database not ready (attempt %s/%s), waiting...",
                retry_count,
                max_retries,
                extra={"error": str(e)},
            )
            await asyncio.sleep(2)

    logger.error("❌ Database failed to become ready after 60 seconds")
    sys.exit(1)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup (wait for the database, migrate to head), then shutdown (close DB + Redis).

    The lifespan handler replaces the deprecated on_event hooks; the bodies are unchanged.
    """
    logger.info(
        "🚀 Starting Analytics Collector API with Dashboard",
        extra={
            "version": settings.APP_VERSION,
            "build_timestamp": settings.BUILD_TIMESTAMP,
        },
    )
    await wait_for_database()
    await run_migrations()
    # A session-state row lost from the table comes back at a fresh generation: everyone signs in
    # again, no revoked cookie returns
    await ensure_session_generation()
    logger.info("✅ Application startup completed successfully!")

    yield

    logger.info("Shutting down Analytics Collector API")
    await async_engine.dispose()
    logger.info("Database connections closed")
    try:
        await close_redis_client()
        logger.info("Redis connection closed")
    except (RedisError, OSError) as e:
        # Shutdown proceeds either way; record that the close failed instead of hiding it.
        logger.warning("Redis close failed during shutdown", extra={"error": str(e)})


def _publish_ingest_schemas(app: FastAPI) -> None:
    """Add the ingest body's models (EventCreate, BatchEventRequest) to the OpenAPI components.
    The ingest routes declare their body by reference (INGEST_OPENAPI), not through a parameter,
    so FastAPI does not collect them. app.openapi() caches the dict it returns and serves it."""
    components = app.openapi().setdefault("components", {}).setdefault("schemas", {})
    components.update(INGEST_BODY_COMPONENTS)


# Exact paths (the guard matches no pattern): the DSN route `/api/v1/events/{project_id}` is not
# listed and stays held to the guard. A literal, because fw.auth_posts_refuse_cross_site must read
# the exemptions; fw.urls_from_route_names reds it as a literal path (escalated: the two conflict).
INGEST_PATHS = ("/api/v1/events/", "/api/v1/events/public")


def create_application() -> FastAPI:
    """Create and configure FastAPI application."""

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Analytics Event Collector API with Dashboard",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # Setup OpenTelemetry
    setup_telemetry(app)

    # Static files (for CSS, JS, images)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # Include routers — web (HTMX/HTML) and API (JSON) separated. First, so the middleware below
    # can name the paths it treats specially by route name.
    app.include_router(web_router)
    app.include_router(api_router)

    # Middleware. Starlette's add_middleware PREPENDS, so the LAST one added is the OUTERMOST.
    # Effective order, outer -> inner: AllowedHosts, SameOrigin, CORS, RateLimit, Logging,
    # SecurityHeaders, Session. Host validation and the cross-site refusal (login CSRF,
    # FLEET-AUTH-SESSION-STANDARD §5) run before anything else touches the request.
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_session_secret(),
        session_cookie="analytics_session",
        max_age=settings.DASHBOARD_SESSION_TIMEOUT,
        same_site="lax",
        https_only=True,
    )
    app.add_middleware(SecurityHeadersMiddleware, environment=settings.ENVIRONMENT)
    app.add_middleware(LoggingMiddleware)
    # RequestSizeLimitMiddleware disabled — nginx handles body size via client_max_body_size
    app.add_middleware(
        RateLimitMiddleware,
        exempt_paths=(
            app.url_path_for("system.health"),
            app.url_path_for("system.metrics"),
            *(p for p in (app.docs_url, app.openapi_url) if p),
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
    # The SDK ingest routes authenticate by HMAC or DSN, never by a cookie, so a cross-site POST
    # there carries no victim's session: a WebView client (CORS_ORIGINS) posts with its own Origin.
    app.add_middleware(
        SameOriginMiddleware,
        allowed_hosts=settings.ALLOWED_HOSTS,
        cross_site_paths=INGEST_PATHS,
    )
    app.add_middleware(
        AllowedHostsMiddleware,
        allowed_hosts=settings.ALLOWED_HOSTS,
        # the container HEALTHCHECK (Dockerfile, compose.yml)
        health_paths=(app.url_path_for("system.health"),),
    )

    # The canonical exception handler renders its error page/partial through this instance.
    app.state.templates = error_templates
    app.add_exception_handler(Exception, general_exception_handler)
    # Per-route limits (slowapi): the limiter is found on app.state; a refusal is a 429 + Retry-After.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    # The per-device and per-app limits (after authentication), answered like the IP limit's 429.
    app.add_exception_handler(AppRateLimitExceeded, app_rate_limit_exceeded_handler)

    _publish_ingest_schemas(app)

    return app


app = create_application()
