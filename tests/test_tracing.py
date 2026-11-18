"""Service spans come from the emitted telemetry helpers: their name, attributes and failures."""

from uuid import UUID

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.telemetry import create_service_span, record_exception_in_span
from app.services.core.analytics_core_service import AnalyticsCoreService


def test_span_carries_its_name_and_the_app_id_and_exports_no_end_user_id(
    span_exporter: InMemorySpanExporter,
) -> None:
    event_id = UUID("12345678-1234-5678-1234-567812345678")
    with create_service_span(
        "AppCoreService",
        "get_app_stats",
        app_id="demo",
        event_id=event_id,
        user_id=None,
    ):
        pass

    (span,) = span_exporter.get_finished_spans()
    assert span.name == "AppCoreService.get_app_stats"
    assert span.attributes is not None
    assert span.attributes["code.function.name"] == "AppCoreService.get_app_stats"
    assert span.attributes["app_id"] == "demo"
    assert "user_id" not in span.attributes
    assert span.status.status_code is StatusCode.UNSET


def test_recorded_exception_marks_span_failed(
    span_exporter: InMemorySpanExporter,
) -> None:
    with create_service_span("HealthCoreService", "report"):
        try:
            raise OSError("db unreachable")
        except OSError as e:
            record_exception_in_span(e)

    (span,) = span_exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert [ev.name for ev in span.events] == ["exception"]


def test_an_exception_recorded_then_reraised_is_one_event(
    span_exporter: InMemorySpanExporter,
) -> None:
    with (
        pytest.raises(ValueError),
        create_service_span("EventCoreService", "ingest"),
    ):
        try:
            raise ValueError("bad payload")
        except ValueError as e:
            record_exception_in_span(e)
            raise

    (span,) = span_exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert [ev.name for ev in span.events] == ["exception"]


@pytest.mark.db
async def test_a_core_service_span_is_the_emitted_helpers(
    db: AsyncSession, span_exporter: InMemorySpanExporter
) -> None:
    await AnalyticsCoreService(db).search_events(query="screen")

    spans = [
        s for s in span_exporter.get_finished_spans() if s.name == "AnalyticsCoreService.search_events"
    ]
    assert len(spans) == 1
    assert spans[0].attributes is not None
    assert spans[0].attributes["code.function.name"] == "AnalyticsCoreService.search_events"
    assert "code.namespace" not in spans[0].attributes
