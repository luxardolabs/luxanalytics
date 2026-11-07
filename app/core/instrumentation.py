"""The app's OpenTelemetry instrumentors and the OTEL settings the tracing module reads.

`app/core/telemetry.py` (emitted) configures the provider from the standard `OTEL_*` environment;
`app/main.py` calls these right after `configure_tracing()`.
"""

import os

from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.trace import TracerProvider
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import settings


def export_otel_settings() -> None:
    """Hand the endpoint Settings holds (a `.env` file included) to the OTEL environment, which
    the tracing module and the exporter read. An endpoint already in the environment wins."""
    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        os.environ.setdefault(
            "OTEL_EXPORTER_OTLP_ENDPOINT", settings.OTEL_EXPORTER_OTLP_ENDPOINT
        )


def instrument_database(
    engine: AsyncEngine, tracer_provider: TracerProvider | None = None
) -> None:
    """A span per statement on this engine (its sync core: an async engine is not instrumentable
    itself). `tracer_provider` defaults to the global one."""
    # skip_dep_check: instrumentor 0.66b1 declares sqlalchemy < 2.1 and so instruments nothing on the
    # fleet's 2.1; the pair is measured and the bypass sanctioned (luxarch --playbook
    # otel-instrumentors). The rule reds it once a covering release is locked: then it comes out.
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine,
        tracer_provider=tracer_provider,
        skip_dep_check=True,
    )
