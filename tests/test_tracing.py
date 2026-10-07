"""The canonical span helpers produce real spans: name, entity ids, and ERROR on failure."""

from uuid import UUID

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.core.tracing import create_service_span, record_exception_in_span

EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)


@pytest.fixture(autouse=True)
def clear_spans() -> None:
    EXPORTER.clear()


def test_span_carries_name_and_entity_ids() -> None:
    event_id = UUID("12345678-1234-5678-1234-567812345678")
    with create_service_span(
        "AppCoreService",
        "get_app_stats",
        app_id="demo",
        event_id=event_id,
        user_id=None,
    ):
        pass

    (span,) = EXPORTER.get_finished_spans()
    assert span.name == "AppCoreService.get_app_stats"
    assert span.attributes is not None
    assert span.attributes["code.namespace"] == "AppCoreService"
    assert span.attributes["app_id"] == "demo"
    assert span.attributes["event_id"] == str(event_id)
    assert "user_id" not in span.attributes
    assert span.status.status_code is StatusCode.UNSET


def test_recorded_exception_marks_span_failed() -> None:
    with create_service_span("HealthCoreService", "report"):
        try:
            raise OSError("db unreachable")
        except OSError as e:
            record_exception_in_span(e)

    (span,) = EXPORTER.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert [ev.name for ev in span.events] == ["exception"]


def test_escaping_exception_marks_span_failed() -> None:
    with pytest.raises(ValueError), create_service_span("EventCoreService", "ingest"):
        raise ValueError("bad payload")

    (span,) = EXPORTER.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
