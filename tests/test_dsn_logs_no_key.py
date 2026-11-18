"""No DSN key (public_id) reaches the logs on any DSN auth path: success, refusal or mismatch."""

import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_application
from app.models.app_model import App
from tests.test_app_rate_limit import _dsn, _event

pytestmark = pytest.mark.db


@pytest_asyncio.fixture
async def server() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=create_application(), raise_app_exceptions=False),
        base_url="https://localhost",
    ) as ac:
        yield ac


def _logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [str(vars(r)) for r in caplog.records]


async def test_control_project_route_success_logs_no_key(
    server: AsyncClient, sample_app: App, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = await server.post(
            f"/api/v1/events/{sample_app.project_id}",
            content=_event(),
            headers=_dsn(sample_app.public_id),
        )
    assert r.status_code == 200, r.text
    assert any("Processing events via project endpoint" in x for x in _logged(caplog))
    assert not any(sample_app.public_id in x for x in _logged(caplog))


async def test_public_route_success_logs_no_key(
    server: AsyncClient, sample_app: App, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = await server.post(
            "/api/v1/events/public",
            content=_event(),
            headers=_dsn(sample_app.public_id),
        )
    assert r.status_code == 200, r.text
    leaks = [x for x in _logged(caplog) if sample_app.public_id in x]
    assert not leaks, leaks[0][:400]


async def test_public_route_refusal_logs_no_presented_key(
    server: AsyncClient, sample_app: App, caplog: pytest.LogCaptureFixture
) -> None:
    # A near-miss or rotated key is credential material too.
    last = "y" if sample_app.public_id.endswith("x") else "x"
    presented = sample_app.public_id[:-1] + last
    with caplog.at_level(logging.DEBUG):
        r = await server.post(
            "/api/v1/events/public", content=_event(), headers=_dsn(presented)
        )
    assert r.status_code == 401
    leaks = [x for x in _logged(caplog) if presented in x]
    assert not leaks, leaks[0][:400]
