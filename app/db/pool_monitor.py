"""Database connection pool monitoring and circuit breaker implementation."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool, QueuePool

logger = logging.getLogger(__name__)

# Prometheus metrics for connection pool monitoring
pool_size_gauge = Gauge("db_pool_size", "Current number of connections in pool")
pool_overflow_gauge = Gauge(
    "db_pool_overflow", "Current number of overflow connections"
)
pool_checked_in_gauge = Gauge(
    "db_pool_checked_in", "Number of connections checked back in"
)
pool_checkout_counter = Counter("db_pool_checkouts_total", "Total connection checkouts")
pool_timeout_counter = Counter("db_pool_timeouts_total", "Total connection timeouts")
pool_failure_counter = Counter("db_pool_failures_total", "Total connection failures")
pool_checkout_time = Histogram(
    "db_pool_checkout_seconds", "Time to checkout connection"
)


class CircuitBreaker:
    """Circuit breaker for database connections."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.failure_count = 0
        self.last_failure_time: datetime | None = None
        self.state = "closed"  # closed, open, half-open

    def call(self, func):
        """Decorator for circuit breaker protection."""

        @asynccontextmanager
        async def wrapper(*args, **kwargs):
            if self.state == "open":
                if self._should_attempt_reset():
                    self.state = "half-open"
                else:
                    raise Exception("Circuit breaker is OPEN")

            try:
                result = await func(*args, **kwargs)
                self._on_success()
                return result
            except self.expected_exception as e:
                self._on_failure()
                raise e

        return wrapper

    def _should_attempt_reset(self) -> bool:
        """Check if we should try to reset the circuit."""
        if not self.last_failure_time:
            return False
        return datetime.now() - self.last_failure_time > timedelta(
            seconds=self.recovery_timeout
        )

    def _on_success(self):
        """Handle successful call."""
        self.failure_count = 0
        self.state = "closed"

    def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.error(
                "Circuit breaker opened",
                extra={
                    "failure_count": self.failure_count,
                    "threshold": self.failure_threshold,
                },
            )


class PoolMonitor:
    """Monitor database connection pool health."""

    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=10, recovery_timeout=30, expected_exception=Exception
        )

    async def get_pool_status(self) -> dict[str, Any]:
        """Get current pool status and metrics."""
        pool = self.engine.pool

        if isinstance(pool, NullPool):
            return {"type": "NullPool", "message": "No connection pooling"}

        if isinstance(pool, QueuePool):
            status = {
                "type": "QueuePool",
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "overflow": pool.overflow(),
                "total": pool.size() + pool.overflow(),
                "circuit_breaker_state": self.circuit_breaker.state,
            }

            # Update Prometheus metrics
            pool_size_gauge.set(pool.size())
            pool_overflow_gauge.set(pool.overflow())
            pool_checked_in_gauge.set(pool.checkedin())

            return status

        return {"type": type(pool).__name__, "message": "Unknown pool type"}

    async def monitor_pool_health(self, interval: int = 60):
        """Continuously monitor pool health."""
        while True:
            try:
                status = await self.get_pool_status()

                # Log pool status
                logger.info("Connection pool status", extra={**status})

                # Check for potential issues
                if status.get("type") == "QueuePool":
                    total_connections = status.get("total", 0)
                    checked_in = status.get("checked_in", 0)

                    # Alert if too many connections are in use
                    if checked_in < total_connections * 0.2:
                        logger.warning(
                            "Low available connections in pool",
                            extra={"available": checked_in, "total": total_connections},
                        )

            except Exception as e:
                logger.error("Error monitoring pool health", extra={"error": str(e)})

            await asyncio.sleep(interval)

    @asynccontextmanager
    async def get_connection_with_metrics(self):
        """Get database connection with metrics tracking."""
        start_time = time.time()

        try:
            # Apply circuit breaker
            if self.circuit_breaker.state == "open":
                pool_failure_counter.inc()
                raise Exception("Database circuit breaker is open")

            # Get connection from pool
            async with self.engine.begin() as conn:
                checkout_time = time.time() - start_time
                pool_checkout_time.observe(checkout_time)
                pool_checkout_counter.inc()

                yield conn

        except TimeoutError:
            pool_timeout_counter.inc()
            self.circuit_breaker._on_failure()
            raise
        except Exception:
            pool_failure_counter.inc()
            self.circuit_breaker._on_failure()
            raise


async def optimize_pool_for_load(engine: AsyncEngine, expected_rps: int):
    """Calculate and log optimal pool settings based on expected load."""

    # Basic formula: pool_size = (expected_concurrent_connections * safety_factor)
    # Assuming average query time of 10ms, concurrent connections = RPS * 0.01
    expected_concurrent = int(expected_rps * 0.01)
    safety_factor = 1.5

    recommended_pool_size = int(expected_concurrent * safety_factor)
    recommended_pool_size = max(
        50, min(recommended_pool_size, 200)
    )  # Clamp between 50-200

    recommendations = {
        "expected_rps": expected_rps,
        "expected_concurrent_connections": expected_concurrent,
        "recommended_pool_size": recommended_pool_size,
        "recommended_max_overflow": 0,  # Disable overflow for predictable performance
        "recommended_timeout": 3,  # Fail fast
        "recommended_recycle": 600,  # 10 minutes
        "postgresql_max_connections": recommended_pool_size
        * 2,  # Account for multiple app instances
    }

    logger.info("Database pool optimization recommendations", extra={**recommendations})

    return recommendations
