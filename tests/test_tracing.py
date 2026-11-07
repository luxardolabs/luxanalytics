"""The canonical span helpers produce real spans: name, entity ids, and ERROR on failure."""

from uuid import UUID

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.core.tracing import create_service_span, record_exception_in_span


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
    assert span.attributes["code.namespace"] == "AppCoreService"
    assert span.attributes["app_id"] == "demo"
    assert "event_id" not in span.attributes
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


def test_escaping_exception_marks_span_failed(
    span_exporter: InMemorySpanExporter,
) -> None:
    with pytest.raises(ValueError), create_service_span("EventCoreService", "ingest"):
        raise ValueError("bad payload")

    (span,) = span_exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
