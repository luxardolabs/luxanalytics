"""The FastAPI application: middleware, exception handlers, routers, lifespan."""

import asyncio
import logging
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from alembic.config import Config
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST
from redis.exceptions import RedisError
from slowapi.errors import RateLimitExceeded
from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool
from starlette.middleware.sessions import SessionMiddleware

from alembic import command
from app.api.v1.routers import router as api_router
from app.core.auth import get_session_secret, require_auth
from app.core.config import settings
from app.core.limiter import limiter, rate_limit_exceeded_handler
from app.core.logging_config import configure_logging
from app.core.middleware import (
    LoggingMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.redis_client import get_redis_client
from app.core.telemetry import get_prometheus_metrics, setup_telemetry
from app.db.database import async_engine

# Every model registered on Base.metadata before anything migrates or queries.
from app.models import App, Device, Event  # noqa: F401
from app.schemas.health_schema import (
    DatabaseHealth,
    HealthResponse,
    PoolStats,
    RedisHealth,
)
from app.utils.exception_handlers import general_exception_handler
from app.web.routers import router as web_router
from app.web.templates import error_templates

# Setup logging
configure_logging(
    service="luxanalytics",
    version=settings.APP_VERSION,
    environment=settings.ENVIRONMENT,
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
    logger.info("✅ Application startup completed successfully!")

    yield

    logger.info("Shutting down Analytics Collector API")
    await async_engine.dispose()
    logger.info("Database connections closed")
    try:
        redis = await get_redis_client()
        if redis:
            await redis.close()
            logger.info("Redis connection closed")
    except (RedisError, OSError) as e:
        # Shutdown proceeds either way; record that the close failed instead of hiding it.
        logger.warning("Redis close failed during shutdown", extra={"error": str(e)})


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

    # Middleware. Starlette's add_middleware PREPENDS, so the LAST one added is the OUTERMOST.
    # Effective order, outer -> inner: TrustedHost, CORS, RateLimit, Logging, SecurityHeaders,
    # Session. Host validation and CORS run before anything else touches the request.
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
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts_list,
    )

    # The canonical exception handler renders its error page/partial through this instance.
    app.state.templates = error_templates
    app.add_exception_handler(Exception, general_exception_handler)
    # Per-route limits (slowapi): the limiter is found on app.state; a refusal is a 429 + Retry-After.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # Static files (for CSS, JS, images)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # Include routers — web (HTMX/HTML) and API (JSON) separated
    app.include_router(web_router)
    app.include_router(api_router)

    @app.get("/health", response_model=HealthResponse)
    async def health_check() -> HealthResponse:
        """Health check endpoint with database pool stats."""
        # Pool counters exist on a QueuePool (the async engine's AsyncAdaptedQueuePool is one).
        pool = async_engine.pool
        pool_stats = (
            PoolStats(
                size=pool.size(),
                checked_in=pool.checkedin(),
                checked_out=pool.checkedout(),
                overflow=pool.overflow(),
                total=pool.checkedin() + pool.checkedout(),
                pool_size_setting=settings.DB_POOL_SIZE,
                max_overflow_setting=settings.DB_POOL_MAX_OVERFLOW,
            )
            if isinstance(pool, QueuePool)
            else None
        )

        # Check database connectivity
        database = DatabaseHealth(status="healthy", pool=pool_stats)
        try:
            async with async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as e:
            database = DatabaseHealth(status="unhealthy", error=str(e), pool=pool_stats)

        # Check Redis connectivity
        redis = RedisHealth(status="healthy")
        try:
            redis_client = await get_redis_client()
            if redis_client:
                await redis_client.ping()
            else:
                redis = RedisHealth(status="not configured")
        except (RedisError, OSError) as e:
            redis = RedisHealth(status="unhealthy", error=str(e))

        return HealthResponse(
            status="healthy" if database.status == "healthy" else "degraded",
            timestamp=time.time(),
            version=settings.APP_VERSION,
            build_timestamp=settings.BUILD_TIMESTAMP,
            database=database,
            redis=redis,
        )

    @app.get("/")
    async def root() -> RedirectResponse:
        """Redirect to dashboard."""
        return RedirectResponse(url="/dashboard/overview", status_code=302)

    # Only the dashboard nav (apps_dropdown.html) posts here, from an authenticated page.
    @app.post("/set-app-context", dependencies=[Depends(require_auth)])
    async def set_app_context(request: Request) -> Response:
        """Set the current app context via cookie (used by nav app selector)."""
        form = await request.form()
        app_id = str(form.get("app_id", ""))
        response = Response(status_code=204)
        if app_id:
            response.set_cookie(
                "analytics_app_id",
                app_id,
                max_age=86400 * 30,
                secure=True,
                httponly=True,
                samesite="lax",
            )
        else:
            response.delete_cookie("analytics_app_id")
        return response

    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus metrics endpoint."""
        metrics_data = get_prometheus_metrics()
        return Response(content=metrics_data, media_type=CONTENT_TYPE_LATEST)

    return app


app = create_application()
