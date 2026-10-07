"""The fleet's canonical span helpers (luxarch --playbook observability).

View and core services open a span per method with `create_service_span`; routers never do,
they ride the auto FastAPI span. CRUD needs none: SQLAlchemy auto-instrumentation covers it.
With no tracer provider configured (no OTEL endpoint) these are no-op spans.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

type SpanAttribute = str | int | float | bool | UUID | datetime | None

_tracer = trace.get_tracer("luxanalytics")


@contextmanager
def create_service_span(
    service_name: str, operation: str, **attributes: SpanAttribute
) -> Iterator[Span]:
    """Open `<service_name>.<operation>` as the current span, with the entity ids as attributes.

    An exception that escapes the block is recorded and sets the span's status to ERROR.
    """
    with _tracer.start_as_current_span(f"{service_name}.{operation}") as span:
        span.set_attribute("code.namespace", service_name)
        span.set_attribute("code.function", operation)
        for key, value in attributes.items():
            if value is None:
                continue
            if isinstance(value, UUID | datetime):
                span.set_attribute(key, str(value))
            else:
                span.set_attribute(key, value)
        yield span


def record_exception_in_span(exc: BaseException) -> None:
    """Mark the current span failed with `exc`. Call it before re-raising or handling."""
    span = trace.get_current_span()
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, str(exc)))
