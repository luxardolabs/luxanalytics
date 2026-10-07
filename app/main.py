# Updated main.py file
import asyncio
import sys
import time

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.core.auth import get_session_secret
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.redis_client import get_redis_client
from app.db.database import async_engine
from app.core.middleware import (LoggingMiddleware, RateLimitMiddleware,
                                 RequestSizeLimitMiddleware, SecurityHeadersMiddleware)
from app.core.telemetry import get_prometheus_metrics, setup_telemetry
from app.web.routers import router as web_router
from app.api.v1.routers import router as api_router

# Setup logging
setup_logging()
logger = structlog.get_logger()

# Import models at module level to ensure they're registered with Base
from app.models import App, Device, Event  # noqa: F401, E402  # Register with Base


async def run_migrations():
    """Run database migrations via Alembic."""
    try:
        from alembic import command
        from alembic.config import Config

        from app.db.database import async_engine

        logger.info("Running database migrations via Alembic...")

        alembic_cfg = Config("alembic.ini")

        def _run_upgrade(connection):
            alembic_cfg.attributes["connection"] = connection
            command.upgrade(alembic_cfg, "head")

        async with async_engine.begin() as conn:
            await conn.run_sync(_run_upgrade)

        logger.info("Database migrations completed successfully!")

    except Exception as e:
        logger.error("Database migrations failed!", error=str(e))
        sys.exit(1)


async def wait_for_database():
    """Wait for database to be ready."""
    from sqlalchemy import text

    from app.db.database import async_engine

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
        except Exception as e:
            retry_count += 1
            logger.info(
                f"Database not ready (attempt {retry_count}/{max_retries}), waiting...",
                error=str(e),
            )
            await asyncio.sleep(2)

    logger.error("❌ Database failed to become ready after 60 seconds")
    sys.exit(1)


def create_application() -> FastAPI:
    """Create and configure FastAPI application."""

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Analytics Event Collector API with Dashboard",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
    )

    # Setup OpenTelemetry
    setup_telemetry(app)

    # Security middleware (order matters: outermost first)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts_list,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    # Session middleware
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_session_secret(),
        session_cookie="analytics_session",
        max_age=settings.DASHBOARD_SESSION_TIMEOUT,
        same_site="lax",
        https_only=not settings.DEBUG,
    )

    # Custom middleware
    app.add_middleware(SecurityHeadersMiddleware, environment=settings.ENVIRONMENT)
    app.add_middleware(LoggingMiddleware)
    # RequestSizeLimitMiddleware disabled — nginx handles body size via client_max_body_size
    app.add_middleware(RateLimitMiddleware)

    # Static files (for CSS, JS, images)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # Include routers — web (HTMX/HTML) and API (JSON) separated
    app.include_router(web_router)
    app.include_router(api_router)

    @app.get("/health")
    async def health_check():
        """Health check endpoint with database pool stats."""
        from app.core.redis_client import get_redis_client
        from app.db.database import async_engine

        # Get database pool stats
        pool = async_engine.pool
        db_pool_stats = {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "total": pool.checkedin() + pool.checkedout(),
            "pool_size_setting": settings.DB_POOL_SIZE,
            "max_overflow_setting": settings.DB_POOL_MAX_OVERFLOW,
        }

        # Check database connectivity
        db_status = "healthy"
        try:
            from sqlalchemy import text

            async with async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as e:
            db_status = f"unhealthy: {str(e)}"

        # Check Redis connectivity
        redis_status = "healthy"
        try:
            redis_client = await get_redis_client()
            if redis_client:
                await redis_client.ping()
            else:
                redis_status = "not configured"
        except Exception as e:
            redis_status = f"unhealthy: {str(e)}"

        return {
            "status": "healthy" if db_status == "healthy" else "degraded",
            "timestamp": time.time(),
            "version": settings.APP_VERSION,
            "build_timestamp": settings.BUILD_TIMESTAMP,
            "database": {
                "status": db_status,
                "pool": db_pool_stats,
            },
            "redis": {
                "status": redis_status,
            },
        }

    @app.get("/")
    async def root():
        """Redirect to dashboard."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/dashboard/overview", status_code=302)

    @app.post("/set-app-context")
    async def set_app_context(request: Request):
        """Set the current app context via cookie (used by nav app selector)."""
        from fastapi.responses import Response
        form = await request.form()
        app_id = str(form.get("app_id", ""))
        response = Response(status_code=204)
        if app_id:
            response.set_cookie("analytics_app_id", app_id, max_age=86400 * 30)
        else:
            response.delete_cookie("analytics_app_id")
        return response

    @app.get("/metrics")
    async def metrics():
        """Prometheus metrics endpoint."""
        from fastapi.responses import Response
        from prometheus_client import CONTENT_TYPE_LATEST

        try:
            metrics_data = get_prometheus_metrics()
            return Response(content=metrics_data, media_type=CONTENT_TYPE_LATEST)
        except Exception as e:
            logger.error("Failed to generate metrics", error=str(e))
            return Response(
                content=b"# Error generating metrics\n", media_type="text/plain"
            )

    @app.on_event("startup")
    async def startup_event():
        """Application startup tasks."""
        logger.info(
            "🚀 Starting Analytics Collector API with Dashboard",
            version=settings.APP_VERSION,
            build_timestamp=settings.BUILD_TIMESTAMP,
        )

        # Wait for database to be ready
        await wait_for_database()

        # Run migrations
        await run_migrations()

        logger.info("✅ Application startup completed successfully!")

    @app.on_event("shutdown")
    async def shutdown_event():
        logger.info("Shutting down Analytics Collector API")
        await async_engine.dispose()
        logger.info("Database connections closed")
        try:
            redis = await get_redis_client()
            if redis:
                await redis.close()
                logger.info("Redis connection closed")
        except Exception:
            pass

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled exception", error=str(exc), path=request.url.path, method=request.method)
        if settings.DEBUG:
            raise exc
        return JSONResponse(status_code=500, content={"error": "Internal server error"})

    return app


app = create_application()
