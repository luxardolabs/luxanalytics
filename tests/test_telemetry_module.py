"""The span helpers and the exporting side of the emitted tracing module, as the app uses them: what
a span carries, that an exception is recorded once, and that nothing reaches the exporter unscrubbed."""

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.core.config import settings
from app.core.instrumentation import drop_personal_attributes
from app.core.telemetry import (
    RedactingSpanExporter,
    add_event_to_span,
    configure_tracing,
    create_client_span,
    create_service_span,
    current_trace_ids,
    enrich_span_from_user,
    record_exception_in_span,
    set_span_attribute,
    shutdown_tracing,
)


class _User:
    def __init__(self, user_id: object, tenant_id: object = None) -> None:
        self.id = user_id
        self.tenant_id = tenant_id


def _only(exporter: InMemorySpanExporter) -> ReadableSpan:
    (span,) = exporter.get_finished_spans()
    return span


def test_a_service_span_is_named_for_its_method_and_carries_its_ids(
    span_exporter: InMemorySpanExporter,
) -> None:
    with create_service_span("AppCoreService", "get_app", app_id="demo", missing=None):
        pass

    span = _only(span_exporter)
    assert span.name == "AppCoreService.get_app"
    assert span.kind is SpanKind.INTERNAL
    assert span.attributes is not None
    assert span.attributes["code.function.name"] == "AppCoreService.get_app"
    assert span.attributes["app_id"] == "demo"
    assert "missing" not in span.attributes
    assert span.resource.attributes["service.name"] == settings.OTEL_SERVICE_NAME
    assert span.resource.attributes["service.version"] == settings.APP_VERSION


def test_a_root_span_starts_a_trace_linked_to_the_span_that_dispatched_it(
    span_exporter: InMemorySpanExporter,
) -> None:
    with (
        create_service_span("Jobs", "dispatcher"),
        create_service_span("Jobs", "nightly", root=True),
    ):
        pass

    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    job, dispatcher = spans["Jobs.nightly"], spans["Jobs.dispatcher"]
    assert job.context.trace_id != dispatcher.context.trace_id
    assert [link.context.span_id for link in job.links] == [dispatcher.context.span_id]


def test_a_client_span_is_a_client_span(span_exporter: InMemorySpanExporter) -> None:
    with create_client_span("Mailer", "send", recipients=2):
        pass

    span = _only(span_exporter)
    assert span.kind is SpanKind.CLIENT
    assert span.name == "Mailer.send"


def test_an_exception_recorded_then_raised_is_one_event(
    span_exporter: InMemorySpanExporter,
) -> None:
    with pytest.raises(ValueError), create_service_span("EventCoreService", "ingest"):
        try:
            raise ValueError("bad payload")
        except ValueError as exc:
            record_exception_in_span(exc, step="decode")
            raise

    span = _only(span_exporter)
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes is not None
    assert span.attributes["error.type"] == "ValueError"
    assert [event.name for event in span.events] == ["exception"]
    assert span.events[0].attributes is not None
    assert span.events[0].attributes["step"] == "decode"


def test_an_exception_group_with_an_error_fails_the_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    with (
        pytest.raises(BaseExceptionGroup),
        create_service_span("EventCoreService", "ingest_batch"),
    ):
        raise ExceptionGroup("batch", [ValueError("one"), KeyError("two")])

    assert _only(span_exporter).status.status_code is StatusCode.ERROR


def test_cancellation_does_not_fail_the_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    with pytest.raises(KeyboardInterrupt), create_service_span("Jobs", "stopped"):
        raise KeyboardInterrupt

    assert _only(span_exporter).status.status_code is StatusCode.UNSET


def test_the_caller_id_is_set_on_the_span_and_left_out_of_the_export(
    span_exporter: InMemorySpanExporter,
) -> None:
    with create_service_span("DashboardViewService", "page"):
        enrich_span_from_user(_User("u-1", tenant_id="t-9"))
        enrich_span_from_user(None)

    attributes = _only(span_exporter).attributes
    assert attributes is not None
    assert "enduser.id" not in attributes
    assert "tenant.id" not in attributes


def test_attributes_and_events_land_on_the_current_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    with create_service_span("DashboardViewService", "page"):
        set_span_attribute("page", 3)
        set_span_attribute("skipped", None)
        add_event_to_span("rendered", rows=12)

    span = _only(span_exporter)
    assert span.attributes is not None
    assert span.attributes["page"] == 3
    assert "skipped" not in span.attributes
    assert [event.name for event in span.events] == ["rendered"]


def test_trace_ids_exist_inside_a_span_and_not_outside() -> None:
    assert current_trace_ids() is None
    with create_service_span("AppCoreService", "get_app"):
        ids = current_trace_ids()

    assert ids is not None
    assert len(ids[0]) == 32
    assert len(ids[1]) == 16


def test_configuring_again_returns_the_provider_already_installed(
    span_exporter: InMemorySpanExporter,
) -> None:
    first = configure_tracing("again", version="1", exporter=span_exporter)
    second = configure_tracing("other", version="2")

    assert first is second
    assert first is not None


def test_the_app_withholds_exception_text_from_the_start() -> None:
    assert configure_tracing("x", version="1", exception_text="withhold") is not None


def test_shutting_down_flushes_and_leaves_the_session_provider_recording(
    span_exporter: InMemorySpanExporter,
) -> None:
    shutdown_tracing()

    with create_service_span("AppCoreService", "after_shutdown"):
        pass

    assert [span.name for span in span_exporter.get_finished_spans()] == [
        "AppCoreService.after_shutdown"
    ]


def _failed_span(exporter: InMemorySpanExporter) -> ReadableSpan:
    exporter.clear()
    with pytest.raises(RuntimeError), create_service_span("AppCoreService", "boom"):
        set_span_attribute("url.path", "/dashboard/user/alice")
        raise RuntimeError("password=hunter2")
    return _only(exporter)


def test_a_redactor_scrubs_span_attributes_and_the_status(
    span_exporter: InMemorySpanExporter,
) -> None:
    failed = _failed_span(span_exporter)
    seen = InMemorySpanExporter()

    def scrub(fields: dict[str, object]) -> dict[str, object]:
        return dict.fromkeys(fields, "SCRUBBED")

    RedactingSpanExporter(seen, scrub).export([failed])

    out = _only(seen)
    assert out.attributes is not None
    assert set(out.attributes.values()) == {"SCRUBBED"}
    assert out.status.description == "SCRUBBED"


def test_a_redactor_that_raises_drops_the_fields_it_was_given(
    span_exporter: InMemorySpanExporter,
) -> None:
    failed = _failed_span(span_exporter)
    seen = InMemorySpanExporter()

    def broken(fields: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("redactor down")

    RedactingSpanExporter(seen, broken).export([failed])

    assert _only(seen).attributes == {"redaction_error": "RuntimeError"}


def test_withheld_exception_text_exports_no_message(
    span_exporter: InMemorySpanExporter,
) -> None:
    failed = _failed_span(span_exporter)
    seen = InMemorySpanExporter()

    RedactingSpanExporter(seen, withhold_exception_text=True).export([failed])

    out = _only(seen)
    assert "hunter2" not in repr(out.status.description)
    assert all("hunter2" not in repr(event.attributes) for event in out.events)


def test_flushing_and_shutting_down_reach_the_wrapped_exporter() -> None:
    seen = InMemorySpanExporter()
    exporter = RedactingSpanExporter(seen)

    assert exporter.force_flush()
    exporter.shutdown()


@pytest.mark.parametrize(
    "key",
    [
        "http.request.header.cookie",
        "http.request.header.x_forwarded_for",
        "http.request.header.user_agent",
        "http.request.header.authorization",
        "http.response.header.set_cookie",
    ],
)
def test_a_captured_header_is_not_exported(key: str) -> None:
    kept = drop_personal_attributes({key: ("secret",), "app_id": "demo"})

    assert kept == {"app_id": "demo"}


def test_a_captured_cookie_does_not_reach_the_exporter() -> None:
    seen = InMemorySpanExporter()
    span = ReadableSpan(
        name="GET /login",
        attributes={
            "http.user_agent": "agent",
            "http.request.header.cookie": ("analytics_session=secret",),
            "app_id": "demo",
        },
    )

    RedactingSpanExporter(seen, drop_personal_attributes).export([span])

    assert _only(seen).attributes == {"app_id": "demo"}
