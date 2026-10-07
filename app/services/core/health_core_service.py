"""HealthCoreService — the liveness report: database reachability + pool counters, Redis."""

import time

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.core.tracing import create_service_span, record_exception_in_span
from app.crud.health_crud import ping_database, pool_counters
from app.schemas.health_schema import (
    DatabaseHealth,
    HealthResponse,
    PoolStats,
    RedisHealth,
)


class HealthCoreService:
    async def report(self) -> HealthResponse:
        """`status` is "healthy" when the database answers, otherwise "degraded"."""
        with create_service_span("HealthCoreService", "report"):
            counters = pool_counters()
            pool = None
            if counters is not None:
                size, checked_in, checked_out, overflow = counters
                pool = PoolStats(
                    size=size,
                    checked_in=checked_in,
                    checked_out=checked_out,
                    overflow=overflow,
                    total=checked_in + checked_out,
                    pool_size_setting=settings.DB_POOL_SIZE,
                    max_overflow_setting=settings.DB_POOL_MAX_OVERFLOW,
                )

            database = DatabaseHealth(status="healthy", pool=pool)
            try:
                await ping_database()
            except (SQLAlchemyError, OSError) as e:
                record_exception_in_span(e)
                database = DatabaseHealth(status="unhealthy", error=str(e), pool=pool)

            redis = RedisHealth(status="healthy")
            try:
                redis_client = await get_redis_client()
                if redis_client:
                    await redis_client.ping()
                else:
                    redis = RedisHealth(status="not configured")
            except (RedisError, OSError) as e:
                record_exception_in_span(e)
                redis = RedisHealth(status="unhealthy", error=str(e))

            return HealthResponse(
                status="healthy" if database.status == "healthy" else "degraded",
                timestamp=time.time(),
                version=settings.APP_VERSION,
                build_commit=settings.BUILD_COMMIT,
                build_timestamp=settings.BUILD_TIMESTAMP,
                database=database,
                redis=redis,
            )
