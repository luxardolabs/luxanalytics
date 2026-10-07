"""OpenTelemetry and Prometheus setup for observability."""

import logging
import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.metrics import Counter, Histogram, UpDownCounter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import generate_latest

from app.core.config import settings
from app.db.database import async_engine

logger = logging.getLogger(__name__)


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
    SQLAlchemyInstrumentor().instrument(
        engine=async_engine.sync_engine, service="ll_analytics_db"
    )
    logger.info(
        "OpenTelemetry initialized",
        extra={"endpoint": otel_endpoint, "service_name": service_name},
    )


def get_prometheus_metrics() -> bytes:
    """The scrape body: prometheus_client's default registry, where every collector registers
    (the process and platform collectors, and the db_pool_* metrics)."""
    return generate_latest()


def create_custom_metrics() -> dict[str, Counter | Histogram | UpDownCounter]:
    """Create custom application metrics."""
    meter = metrics.get_meter("ll_analytics")

    # Request counter
    request_counter = meter.create_counter(
        name="http_requests_total", description="Total HTTP requests", unit="1"
    )

    # Request duration histogram
    request_duration = meter.create_histogram(
        name="http_request_duration_seconds",
        description="HTTP request duration",
        unit="s",
    )

    # Active connections gauge
    active_connections = meter.create_up_down_counter(
        name="active_connections", description="Number of active connections", unit="1"
    )

    # Events processed counter
    events_processed = meter.create_counter(
        name="events_processed_total", description="Total events processed", unit="1"
    )

    return {
        "request_counter": request_counter,
        "request_duration": request_duration,
        "active_connections": active_connections,
        "events_processed": events_processed,
    }
