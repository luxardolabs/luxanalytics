"""OpenTelemetry and Prometheus setup for observability."""

import logging
import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import TracerProvider as TracerProviderAPI
from prometheus_client import Gauge, generate_latest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import settings
from app.crud.health_crud import pool_counters
from app.db.database import async_engine

logger = logging.getLogger(__name__)

# The database pool, read from the engine at each scrape (the same counters /health reports).
POOL_SIZE = Gauge("db_pool_size", "Configured pool size (DB_POOL_SIZE)")
POOL_CHECKED_OUT = Gauge("db_pool_checked_out", "Pooled connections in use")
POOL_OVERFLOW = Gauge("db_pool_overflow", "Connections open beyond the pool size")


def setup_telemetry(app: FastAPI | None = None) -> None:
    """Initialize OpenTelemetry tracing and metrics."""

    # Skip if no OTEL endpoint configured
    otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not otel_endpoint:
        logger.info("OpenTelemetry disabled - no endpoint configured")
        return

    # No try/except: the OTLP exporter connects lazily, so anything raised here is a config or
    # code error, and an operator who set the endpoint wants it to fail loudly, not trace nothing.
    service_name = os.getenv("OTEL_SERVICE_NAME", "ll_analytics")
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": settings.APP_VERSION,
            "deployment.environment": settings.ENVIRONMENT,
        }
    )

    # Keep the SDK provider we built: trace.get_tracer_provider() returns the API type (no
    # add_span_processor), and a provider set earlier would be returned instead.
    tracer_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(tracer_provider)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otel_endpoint, insecure=True))
    )
    metrics.set_meter_provider(MeterProvider(resource=resource))

    if app:
        FastAPIInstrumentor.instrument_app(app)
    instrument_database(async_engine)
    logger.info(
        "OpenTelemetry initialized",
        extra={"endpoint": otel_endpoint, "service_name": service_name},
    )


def instrument_database(
    engine: AsyncEngine, tracer_provider: TracerProviderAPI | None = None
) -> None:
    """A span per statement on this engine (its sync core: an async engine is not instrumentable
    itself). `tracer_provider` defaults to the global one."""
    # skip_dep_check: instrumentor 0.66b1 declares sqlalchemy < 2.1 and so instruments nothing on the
    # fleet's 2.1; the pair is measured and the bypass sanctioned (luxarch --playbook
    # otel-instrumentors). The rule reds it once a covering release is locked: then it comes out.
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine,
        service="ll_analytics_db",
        tracer_provider=tracer_provider,
        skip_dep_check=True,
    )


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
