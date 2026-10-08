"""The request log names its fields; credentials in headers never reach it."""

import logging

import pytest
from httpx import AsyncClient


@pytest.mark.db
async def test_request_log_carries_no_credentials(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "Basic c2VjcmV0LXZhbHVlLW5ldmVyLWxvZ2dlZA=="
    with caplog.at_level(
        logging.DEBUG, logger="app.core.middleware.request_middleware"
    ):
        await client.post(
            "/api/v1/events",
            content=b"{}",
            headers={"Authorization": secret, "Cookie": "analytics_session=s3cret"},
        )
    logged = [str(vars(r)) for r in caplog.records]
    assert any("Request started" in line for line in logged)  # the request was logged
    assert not any(secret in line or "s3cret" in line for line in logged)
