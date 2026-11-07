"""The Prometheus scrape: the default registry plus the database pool gauges."""

from prometheus_client import Gauge, generate_latest

from app.crud.health_crud import pool_counters

# The database pool, read from the engine at each scrape (the same counters /health reports).
POOL_SIZE = Gauge("db_pool_size", "Configured pool size (DB_POOL_SIZE)")
POOL_CHECKED_OUT = Gauge("db_pool_checked_out", "Pooled connections in use")
POOL_OVERFLOW = Gauge("db_pool_overflow", "Connections open beyond the pool size")


def get_prometheus_metrics() -> bytes:
    """The scrape body: prometheus_client's default registry, where every collector registers
    (the process and platform collectors, and the db_pool_* gauges set here)."""
    counters = pool_counters()
    if counters is not None:
        size, _checked_in, checked_out, overflow = counters
        POOL_SIZE.set(size)
        POOL_CHECKED_OUT.set(checked_out)
        # QueuePool.overflow() counts up from -pool_size; the series means connections beyond it.
        POOL_OVERFLOW.set(max(0, overflow))
    return generate_latest()
