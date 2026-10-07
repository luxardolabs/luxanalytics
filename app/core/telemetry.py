"""OpenTelemetry and Prometheus setup for observability."""

import logging
import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CollectorRegistry, generate_latest

from app.core.config import settings

logger = logging.getLogger(__name__)

# Global registry for Prometheus metrics
REGISTRY = CollectorRegistry()


def setup_telemetry(app=None):
    """Initialize OpenTelemetry tracing and metrics."""

    # Skip if no OTEL endpoint configured
    otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not otel_endpoint:
        logger.info("OpenTelemetry disabled - no endpoint configured")
        return

    try:
        # Create resource identifying the service
        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "ll_analytics"),
                "service.version": settings.APP_VERSION,
                "deployment.environment": settings.ENVIRONMENT,
            }
        )

        # Setup tracing
        trace.set_tracer_provider(TracerProvider(resource=resource))
        tracer_provider = trace.get_tracer_provider()

        # Add OTLP exporter
        otlp_exporter = OTLPSpanExporter(
            endpoint=otel_endpoint,
            insecure=True,  # Use insecure for internal communication
        )
        span_processor = BatchSpanProcessor(otlp_exporter)
        tracer_provider.add_span_processor(span_processor)

        # Setup metrics
        metrics.set_meter_provider(MeterProvider(resource=resource))

        # Auto-instrument FastAPI
        if app:
            FastAPIInstrumentor.instrument_app(app)
            logger.info("FastAPI instrumentation enabled")

        # Auto-instrument SQLAlchemy
        try:
            from app.db.database import async_engine

            SQLAlchemyInstrumentor().instrument(
                engine=async_engine.sync_engine, service="ll_analytics_db"
            )
            logger.info("SQLAlchemy instrumentation enabled")
        except Exception as e:
            logger.warning("Failed to instrument SQLAlchemy", extra={"error": str(e)})

        logger.info(
            "OpenTelemetry initialized",
            extra={
                "endpoint": otel_endpoint,
                "service_name": os.getenv("OTEL_SERVICE_NAME", "ll_analytics"),
            },
        )

    except Exception as e:
        logger.error("Failed to initialize OpenTelemetry", extra={"error": str(e)})


def get_prometheus_metrics() -> bytes:
    """Generate Prometheus metrics in text format."""
    # Import here to ensure metrics are registered

    # Generate and return metrics
    return generate_latest(REGISTRY)


def create_custom_metrics():
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
