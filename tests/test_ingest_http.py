"""POST /api/v1/events/{project_id}: the Sentry-style DSN ingest path, over HTTP.

A malformed event is the CALLER's error. It used to be a 500, because one broad `except Exception`
around the whole ingest turned every failure (bad payload shape, a field that fails validation)
into "Internal server error": an SDK cannot tell bad data it should drop from an outage it should
retry.
"""

import base64

import pytest
from httpx import AsyncClient

PROJECT = "1234567890123456"
PUBLIC_ID = "test_public_id_12345678901"


def _dsn_auth(public_id: str = PUBLIC_ID) -> dict[str, str]:
    token = base64.b64encode(f"{public_id}:".encode()).decode()
    return {"Authorization": f"Basic {token}"}


EVENT = {"name": "app_opened", "timestamp": "2026-10-06T12:00:00Z", "metadata": {}}


@pytest.mark.db
async def test_a_valid_event_is_accepted(
    client: AsyncClient, sample_app: object
) -> None:
    response = await client.post(
        f"/api/v1/events/{PROJECT}", json=EVENT, headers=_dsn_auth()
    )
    assert response.status_code == 200
    assert response.json()["events_received"] == 1


@pytest.mark.db
async def test_a_batch_is_accepted(client: AsyncClient, sample_app: object) -> None:
    response = await client.post(
        f"/api/v1/events/{PROJECT}",
        json={"events": [EVENT, EVENT]},
        headers=_dsn_auth(),
    )
    assert response.status_code == 200
    assert response.json()["events_received"] == 2


@pytest.mark.db
async def test_an_invalid_event_is_a_client_error_not_a_500(
    client: AsyncClient, sample_app: object
) -> None:
    response = await client.post(
        f"/api/v1/events/{PROJECT}",
        json={"timestamp": "2026-10-06T12:00:00Z"},
        headers=_dsn_auth(),
    )
    assert response.status_code == 422


@pytest.mark.db
async def test_a_payload_of_the_wrong_shape_is_a_400_not_a_500(
    client: AsyncClient, sample_app: object
) -> None:
    response = await client.post(
        f"/api/v1/events/{PROJECT}", json=42, headers=_dsn_auth()
    )
    assert response.status_code == 400


@pytest.mark.db
async def test_undecodable_json_is_a_400(
    client: AsyncClient, sample_app: object
) -> None:
    response = await client.post(
        f"/api/v1/events/{PROJECT}",
        content=b"{not json",
        headers={**_dsn_auth(), "Content-Type": "application/json"},
    )
    assert response.status_code == 400


@pytest.mark.db
async def test_the_wrong_public_id_is_refused(
    client: AsyncClient, sample_app: object
) -> None:
    response = await client.post(
        f"/api/v1/events/{PROJECT}",
        json=EVENT,
        headers=_dsn_auth("not_the_public_id_000000000"),
    )
    assert response.status_code == 401


@pytest.mark.db
async def test_an_unknown_project_is_a_404(
    client: AsyncClient, sample_app: object
) -> None:
    response = await client.post(
        "/api/v1/events/0000000000000000", json=EVENT, headers=_dsn_auth()
    )
    assert response.status_code == 404
