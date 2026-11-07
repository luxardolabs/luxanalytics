"""What a traced request exports: one server span, route templates instead of path and query values,
no end-user id, no client address or agent, no free text from a rejected event, a trace of the app's
own whatever the caller's headers say, and no exporter the app did not configure."""

import json
from base64 import b64encode
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi.telemetry import _runtime
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from starlette.types import Message

from app.core.config import settings
from app.main import create_application

USER_ID = "alice.smith@example.com"
SEARCH = "needle-search-text"
USER_AGENT = "UA-alices-iphone"
CLIENT_IP = "203.0.113.77"
PUBLIC_ID = "test_public_id_12345678901"
PROJECT_ID = "1234567890123456"


@asynccontextmanager
async def _client(*, signed_in: bool = False) -> AsyncIterator[AsyncClient]:
    app = create_application()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://localhost"
    ) as client:
        if signed_in:
            login = await client.post(
                "/login",
                data={
                    "username": settings.DASHBOARD_USERNAME,
                    "password": settings.DASHBOARD_PASSWORD,
                },
                follow_redirects=False,
            )
            assert login.status_code == 303
        yield client


async def _get(
    path: str, *, signed_in: bool = False, headers: dict[str, str] | None = None
) -> int:
    async with _client(signed_in=signed_in) as client:
        return (await client.get(path, headers=headers)).status_code


def _exported(exporter: InMemorySpanExporter) -> str:
    return "\n".join(
        f"{span.name} {dict(span.attributes or {})} "
        f"{[dict(event.attributes or {}) for event in span.events]} {span.status.description}"
        for span in exporter.get_finished_spans()
    )


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


async def test_a_signed_in_profile_exports_the_route_template_and_no_end_user_id(
    span_exporter: InMemorySpanExporter,
) -> None:
    status = await _get(f"/dashboard/user/{USER_ID}?search_q={SEARCH}", signed_in=True)

    assert status == 200
    exported = _exported(span_exporter)
    assert "AnalyticsCoreService.get_user_profile" in exported
    assert "/dashboard/user/{user_id" in exported
    assert USER_ID not in exported
    assert SEARCH not in exported


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


async def test_no_span_carries_the_client_address_or_agent(
    span_exporter: InMemorySpanExporter,
) -> None:
    await _get(
        "/login",
        signed_in=True,
        headers={"User-Agent": USER_AGENT, "X-Forwarded-For": CLIENT_IP},
    )

    exported = _exported(span_exporter)
    assert USER_AGENT not in exported
    assert CLIENT_IP not in exported
    assert "net.peer.ip" not in exported
    assert "net.peer.port" not in exported
    assert "client.address" not in exported


def _event_body(metadata: dict[str, object]) -> bytes:
    return json.dumps(
        {
            "name": "signed_up",
            "timestamp": datetime.now(UTC).isoformat(),
            "metadata": metadata,
        }
    ).encode()


async def _ingest(body: bytes) -> int:
    async with _client() as client:
        response = await client.post(
            f"/api/v1/events/{PROJECT_ID}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Basic "
                + b64encode(f"{PUBLIC_ID}:".encode()).decode(),
            },
        )
        return response.status_code


async def test_a_rejected_event_exports_none_of_its_values(
    span_exporter: InMemorySpanExporter, sample_app: object
) -> None:
    status = await _ingest(
        _event_body({"email": "bob@example.com\x00", "device_id": "d1"})
    )

    assert status == 422
    assert "bob@example" not in _exported(span_exporter)


async def test_an_accepted_event_records_no_exception(
    span_exporter: InMemorySpanExporter, sample_app: object
) -> None:
    status = await _ingest(_event_body({"email": "bob@example.com"}))

    assert status == 200
    assert not [span.name for span in span_exporter.get_finished_spans() if span.events]


async def test_a_caller_cannot_decide_whether_its_request_is_traced(
    span_exporter: InMemorySpanExporter,
) -> None:
    caller_trace = "0af7651916cd43dd8448eb211c80319c"
    await _get(
        "/login",
        headers={"traceparent": f"00-{caller_trace}-b7ad6b7169203331-00"},
    )

    servers = [
        span
        for span in span_exporter.get_finished_spans()
        if span.kind is SpanKind.SERVER
    ]
    assert [span.name for span in servers] == ["GET /login"]
    assert format(servers[0].context.trace_id, "032x") != caller_trace


async def test_starting_the_app_adds_no_exporter_of_its_own(
    monkeypatch: pytest.MonkeyPatch, span_exporter: InMemorySpanExporter
) -> None:
    """FastAPI adds OTLP exporters from the OTEL environment at lifespan startup, beside the
    redacting one, unless the app turns that off."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.delenv("OTEL_METRICS_EXPORTER", raising=False)
    monkeypatch.delenv("OTEL_LOGS_EXPORTER", raising=False)
    app = create_application()

    @asynccontextmanager
    async def nothing(_app: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(app.router, "lifespan_context", nothing)
    incoming: list[Message] = [
        {"type": "lifespan.startup"},
        {"type": "lifespan.shutdown"},
    ]

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(_message: Message) -> None:
        return None

    owned_before = list(_runtime._owned)
    try:
        await app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
        added = [c for c in _runtime._owned if c not in owned_before]
    finally:
        for component in [c for c in _runtime._owned if c not in owned_before]:
            component.shutdown()
        _runtime._owned[:] = owned_before
        _runtime._configured[:] = [
            entry for entry in _runtime._configured if entry[1] in owned_before
        ]

    assert not added
