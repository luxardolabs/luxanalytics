"""Database queries record spans: the SQLAlchemy instrumentor actually instruments the engine
(LUXANALYTI-86, `luxarch --playbook otel-instrumentors`). Measured the playbook's way, with an
in-memory exporter, so a release that silently instruments nothing fails here."""

import os
from collections.abc import AsyncIterator

import pytest
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.telemetry import instrument_database

pytestmark = pytest.mark.db


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield built
    SQLAlchemyInstrumentor().uninstrument()
    await built.dispose()


async def _statements_recorded(engine: AsyncEngine) -> list[object]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # The instrumentor is a process-wide singleton and a second instrument() is a silent no-op:
    # start from uninstrumented so whatever ran before cannot make this measure nothing.
    SQLAlchemyInstrumentor().uninstrument()
    instrument_database(engine, tracer_provider=provider)
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 42"))
    return [
        span.attributes.get("db.statement")
        for span in exporter.get_finished_spans()
        if span.attributes
    ]


async def test_each_statement_records_a_database_span(engine: AsyncEngine) -> None:
    assert "SELECT 42" in await _statements_recorded(engine)


async def test_instrumentation_left_on_earlier_cannot_blind_the_measurement(
    engine: AsyncEngine,
) -> None:
    """Something earlier in the session (an app built with OTEL on) left the instrumentor on, for
    another engine: the measurement must still see this engine's statements (adversarial pass 6)."""
    earlier = create_async_engine(os.environ["TEST_DATABASE_URL"])
    instrument_database(earlier, tracer_provider=TracerProvider())
    try:
        assert "SELECT 42" in await _statements_recorded(engine)
    finally:
        await earlier.dispose()
