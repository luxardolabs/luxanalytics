"""GET /health: the contract the container health check and monitoring read."""

from typing import Literal

from pydantic import BaseModel


class PoolStats(BaseModel):
    """Connection-pool counters, present when the engine runs a QueuePool."""

    size: int
    checked_in: int
    checked_out: int
    overflow: int
    total: int
    pool_size_setting: int
    max_overflow_setting: int


class DatabaseHealth(BaseModel):
    status: Literal["healthy", "unhealthy"]
    error: str | None = None
    pool: PoolStats | None


class RedisHealth(BaseModel):
    status: Literal["healthy", "unhealthy", "not configured"]
    error: str | None = None


class HealthResponse(BaseModel):
    """`status` is "healthy" when the database answers, otherwise "degraded"."""

    status: Literal["healthy", "degraded"]
    timestamp: float
    version: str
    build_timestamp: str | None
    database: DatabaseHealth
    redis: RedisHealth
