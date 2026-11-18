"""No DSN key (public_id) reaches a log line, a span or an error, wherever the client puts it."""

import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import app_crud as app_crud_module
from app.crud.app_crud import AppCRUD
from app.main import create_application
from app.models.app_model import App
from tests.test_app_rate_limit import _dsn, _event

pytestmark = pytest.mark.db


def _logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    out = []
    for r in caplog.records:
        out.append(str(vars(r)))
        out.append(r.getMessage())
        if r.exc_info:
            out.append(logging.Formatter().formatException(r.exc_info))
    return out


def _span_text(exporter: InMemorySpanExporter) -> list[str]:
    out = []
    for s in exporter.get_finished_spans():
        out.append(s.name)
        out.append(repr(dict(s.attributes or {})))
        for e in s.events:
            out.append(e.name + repr(dict(e.attributes or {})))
        out.append(repr(s.status.description))
    return out


@pytest_asyncio.fixture
async def traced(span_exporter: InMemorySpanExporter) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=create_application(), raise_app_exceptions=False),
        base_url="https://localhost",
    ) as ac:
        yield ac


# ---- logs: a DSN key sent in the path (/api/v1/events/<public_id>) --------------------------


async def test_control_project_route_logs_no_key(
    traced: AsyncClient, sample_app: App, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = await traced.post(
            f"/api/v1/events/{sample_app.project_id}",
            content=_event(),
            headers=_dsn(sample_app.public_id),
        )
    assert r.status_code == 200, r.text
    assert not [x for x in _logged(caplog) if sample_app.public_id in x]


async def test_key_in_path_reaches_no_log(
    traced: AsyncClient, sample_app: App, caplog: pytest.LogCaptureFixture
) -> None:
    """A client that puts its key where the project id goes gets a 404, and neither that refusal
    nor the request's debug line logs the path segment."""
    with caplog.at_level(logging.DEBUG):
        r = await traced.post(
            f"/api/v1/events/{sample_app.public_id}",
            content=_event(),
            headers=_dsn(sample_app.public_id),
        )
    assert r.status_code == 404
    # The test client's own httpx line is not the server's.
    caplog.records[:] = [r for r in caplog.records if not r.name.startswith("httpx")]
    leaks = [x for x in _logged(caplog) if sample_app.public_id in x]
    assert not leaks, leaks[0][:400]


# ---- spans ------------------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["public", "project", "refused"])
async def test_no_span_carries_the_key(
    traced: AsyncClient,
    span_exporter: InMemorySpanExporter,
    sample_app: App,
    route: str,
) -> None:
    key = sample_app.public_id
    if route == "refused":
        key = key[:-1] + ("y" if key.endswith("x") else "x")
    path = (
        f"/api/v1/events/{sample_app.project_id}"
        if route == "project"
        else "/api/v1/events/public"
    )
    r = await traced.post(path, content=_event(), headers=_dsn(key))
    assert r.status_code in (200, 401), r.text
    spans = _span_text(span_exporter)
    assert spans, "no spans recorded: the measurement saw nothing"
    assert any("SELECT" in x for x in spans), "no db.statement span: " + repr(spans)[:600]
    leaks = [x for x in spans if key in x]
    assert not leaks, leaks[0][:400]


async def test_key_in_path_reaches_no_span(
    traced: AsyncClient, span_exporter: InMemorySpanExporter, sample_app: App
) -> None:
    r = await traced.post(
        f"/api/v1/events/{sample_app.public_id}",
        content=_event(),
        headers=_dsn(sample_app.public_id),
    )
    assert r.status_code == 404
    spans = _span_text(span_exporter)
    assert any(x.startswith("POST") for x in spans), "no server span: " + repr(spans)[:900]
    assert any("/api/v1/events/{project_id}" in x for x in spans), repr(spans)[:1500]
    leaks = [x for x in spans if sample_app.public_id in x]
    assert not leaks, leaks[0][:400]


# ---- the lookup raising: its own statement, failed by Postgres ---------------------------------


async def test_lookup_failure_logs_no_key(
    traced: AsyncClient,
    span_exporter: InMemorySpanExporter,
    sample_app: App,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = app_crud_module.AppCRUD.get_by_public_id

    async def failing(self: AppCRUD, db: AsyncSession, public_id: str) -> App | None:
        # The real lookup statement, failed by the server (its table not on the search path): a
        # DBAPIError wrapping the lookup's own SQL and bound key.
        await db.execute(text("SET LOCAL search_path TO pg_catalog"))
        return await real(self, db, public_id)

    monkeypatch.setattr(app_crud_module.AppCRUD, "get_by_public_id", failing)
    with caplog.at_level(logging.DEBUG):
        r = await traced.post(
            "/api/v1/events/public",
            content=_event(),
            headers=_dsn(sample_app.public_id),
        )
    assert r.status_code == 500, r.text
    assert sample_app.public_id not in r.text
    logged = _logged(caplog)
    assert any("unhandled_exception" in x for x in logged)
    leaks = [x for x in logged if sample_app.public_id in x]
    leaks += [x for x in _span_text(span_exporter) if sample_app.public_id in x]
    assert not leaks, leaks[0][:600]


# ---- uvicorn's access line (path) is off once the app is imported under uvicorn's own config ----


def test_uvicorn_access_log_is_off_after_uvicorn_configures_logging() -> None:
    """In a fresh process, as `uvicorn app.main:app` runs: uvicorn configures logging, then imports
    the app. Control: before the import, the access logger is on."""
    import subprocess
    import sys

    probe = (
        "import logging, uvicorn\n"
        "c = uvicorn.Config('app.main:app', host='127.0.0.1', port=0)\n"
        "print('before', logging.getLogger('uvicorn.access').isEnabledFor(logging.INFO))\n"
        "c.load()\n"
        "print('after', logging.getLogger('uvicorn.access').isEnabledFor(logging.INFO))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60
    )
    assert "before True" in out.stdout, out.stdout + out.stderr[-2000:]
    assert "after False" in out.stdout, out.stdout + out.stderr[-2000:]
