"""A request is one server span, and what it exports carries route templates, not what the caller
wrote: no end-user id in the path, no search text in the query."""

from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from app.main import create_application

USER_ID = "alice.smith@example.com"
SEARCH = "needle-search-text"


async def _get(path: str) -> None:
    app = create_application()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://localhost"
    ) as client:
        await client.get(path)


async def test_a_request_records_one_server_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    await _get("/login")

    servers = [
        span
        for span in span_exporter.get_finished_spans()
        if span.kind is SpanKind.SERVER
    ]
    assert [span.name for span in servers] == ["GET /login"]


async def test_server_spans_carry_the_route_template_and_no_path_values(
    span_exporter: InMemorySpanExporter,
) -> None:
    await _get(f"/dashboard/user/{USER_ID}?search_q={SEARCH}")

    exported = [
        f"{span.name} {dict(span.attributes or {})}"
        for span in span_exporter.get_finished_spans()
    ]
    assert exported
    assert not [line for line in exported if USER_ID in line], exported
    assert not [line for line in exported if SEARCH in line], exported


async def test_the_query_keys_are_kept_and_the_values_are_not(
    span_exporter: InMemorySpanExporter,
) -> None:
    await _get(f"/login?next={SEARCH}")

    targets = [
        str(value)
        for span in span_exporter.get_finished_spans()
        for key, value in (span.attributes or {}).items()
        if key in {"http.url", "http.target", "url.query", "url.full"}
    ]
    assert targets
    assert all("next=REDACTED" in target for target in targets if "next" in target)
    assert not [target for target in targets if SEARCH in target]
