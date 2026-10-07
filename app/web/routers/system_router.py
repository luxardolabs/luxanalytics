"""App-level routes: liveness, metrics, the dashboard root redirect and the nav app selector.

Module-level (not defined inside create_application) so every app instance shares these route
objects, like every other router.
"""

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool

from app.core.auth import require_auth
from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.core.telemetry import get_prometheus_metrics
from app.db.database import async_engine
from app.schemas.health_schema import (
    DatabaseHealth,
    HealthResponse,
    PoolStats,
    RedisHealth,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
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


@router.get("/")
async def root() -> RedirectResponse:
    """Redirect to dashboard."""
    return RedirectResponse(url="/dashboard/overview", status_code=302)


# Only the dashboard nav (apps_dropdown.html) posts here, from an authenticated page.
@router.post("/set-app-context", dependencies=[Depends(require_auth)])
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


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    metrics_data = get_prometheus_metrics()
    return Response(content=metrics_data, media_type=CONTENT_TYPE_LATEST)
