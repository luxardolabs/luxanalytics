"""Malformed ingest input is the caller's error, never a 500.

Each of these was a 500 found by an adversarial round: a NUL in the Basic username or in the
project path reached the app lookup, and Postgres text cannot hold a NUL; a deeply nested body
raised RecursionError in json.loads, and an integer over 4300 digits a plain ValueError, neither of
which the body decode caught."""

import base64
import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_application
from app.models.app_model import App
from tests.test_app_rate_limit import _dsn, _event, _signed

pytestmark = pytest.mark.db

PROJECT = "1234567890123456"
PUBLIC_ID = "test_public_id_12345678901"

DEEP = b"[" * 100_000 + b"]" * 100_000
HUGE_INT = b"1" * 4301


@pytest_asyncio.fixture
async def server() -> AsyncIterator[AsyncClient]:
    """Answers a 500 as a response, as a real server does, instead of raising it."""
    async with AsyncClient(
        transport=ASGITransport(app=create_application(), raise_app_exceptions=False),
        base_url="https://localhost",
    ) as ac:
        yield ac


async def test_a_nul_in_the_basic_username_is_a_401(
    server: AsyncClient, sample_app: App
) -> None:
    response = await server.post(
        "/api/v1/events/public",
        content=_event(),
        headers={"Authorization": "Basic YQBiOg==", "Content-Type": "application/json"},
    )
    assert response.status_code == 401


async def test_a_nul_in_the_project_path_is_a_404(server: AsyncClient) -> None:
    response = await server.post("/api/v1/events/%00", content=_event())
    assert response.status_code == 404


@pytest.mark.parametrize("body", [DEEP, HUGE_INT], ids=["deep", "huge_int"])
async def test_an_undecodable_body_is_a_400_on_the_public_dsn_route(
    server: AsyncClient, sample_app: App, body: bytes
) -> None:
    response = await server.post(
        "/api/v1/events/public", content=body, headers=_dsn(PUBLIC_ID)
    )
    assert response.status_code == 400


@pytest.mark.parametrize("body", [DEEP, HUGE_INT], ids=["deep", "huge_int"])
async def test_an_undecodable_body_is_a_400_on_the_project_route(
    server: AsyncClient, sample_app: App, body: bytes
) -> None:
    response = await server.post(
        f"/api/v1/events/{PROJECT}", content=body, headers=_dsn(PUBLIC_ID)
    )
    assert response.status_code == 400


@pytest.mark.parametrize("body", [DEEP, HUGE_INT], ids=["deep", "huge_int"])
async def test_an_undecodable_body_is_a_400_on_the_hmac_route(
    server: AsyncClient, sample_app: App, body: bytes
) -> None:
    response = await server.post(
        "/api/v1/events/", content=body, headers=_signed(body, sample_app.app_id)
    )
    assert response.status_code == 400


async def test_an_hmac_mismatch_logs_no_part_of_the_correct_signature(
    server: AsyncClient, sample_app: App, caplog: pytest.LogCaptureFixture
) -> None:
    body = _event()
    headers = _signed(body, sample_app.app_id)
    correct = headers["X-HMAC-Signature"]
    wrong = ("0" if correct[0] != "0" else "1") * len(correct)

    with caplog.at_level(logging.WARNING):
        response = await server.post(
            "/api/v1/events/",
            content=body,
            headers={**headers, "X-HMAC-Signature": wrong},
        )

    assert response.status_code == 403
    [record] = [
        r
        for r in caplog.records
        if r.getMessage() == "HMAC signature verification failed"
    ]
    assert record.__dict__["key_id"] == sample_app.app_id
    logged = repr(record.__dict__)
    assert "computed" not in record.__dict__
    # No 8-hex-character window of the correct signature reaches the record.
    windows = {correct[i : i + 8] for i in range(len(correct) - 7)}
    assert not [w for w in windows if w in logged]


async def test_a_project_dsn_key_mismatch_logs_no_part_of_the_real_public_id(
    server: AsyncClient, sample_app: App, caplog: pytest.LogCaptureFixture
) -> None:
    wrong = base64.b64encode(b"wrong_key_entirely_000000:").decode()
    with caplog.at_level(logging.WARNING):
        response = await server.post(
            f"/api/v1/events/{PROJECT}",
            content=_event(),
            headers={
                "Authorization": f"Basic {wrong}",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 401
    [record] = [
        r for r in caplog.records if r.getMessage() == "Invalid public_id for project"
    ]
    assert record.__dict__["project_id"] == PROJECT
    # Neither key reaches the record: not the one that would have been accepted, not the one sent.
    assert "expected_public_id" not in record.__dict__
    assert "provided_public_id" not in record.__dict__
    logged = repr(record.__dict__)
    assert PUBLIC_ID[:8] not in logged
    assert "wrong_ke" not in logged
