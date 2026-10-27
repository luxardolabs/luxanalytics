"""The per-app ingest rate limit counts only requests that proved they are the app.

It was keyed on the unverified X-Key-ID header and counted before authentication, so anyone could
spend an app's budget with junk requests carrying its (public) key id and the app's own signed
events got 429. DSN ingest sends no X-Key-ID, so it was never limited per app at all (adversarial
audit 2026-10-27, LUXANALYTI-97).
"""

import hashlib
import hmac
import json
import time
from base64 import b64encode
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import create_application
from app.models.app_model import App
from tests.test_ingest_http import PUBLIC_ID

pytestmark = pytest.mark.db

LIMIT = 5


@pytest.fixture
def limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both limiters at LIMIT requests per window, so a test can reach them."""
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", LIMIT)


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_application()), base_url="https://localhost"
    )


def _event() -> bytes:
    return json.dumps(
        {
            "name": "app_opened",
            "timestamp": datetime.now(UTC).isoformat(),
            "metadata": {},
        }
    ).encode()


def _signed(body: bytes, key_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    secret = settings.hmac_keys_dict[key_id]
    signature = hmac.new(
        secret.encode(), body + timestamp.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-HMAC-Signature": signature,
        "X-Key-ID": key_id,
        "X-Timestamp": timestamp,
    }


async def test_unauthenticated_requests_cannot_spend_an_apps_budget(
    limited: None, sample_app: App
) -> None:
    async with _client() as ac:
        # One IP with no secret sends junk carrying the app's key id, up to the limit.
        for _ in range(LIMIT):
            junk = await ac.post(
                "/api/v1/events/",
                content=b"{}",
                headers={
                    "X-Key-ID": sample_app.app_id,
                    "X-HMAC-Signature": "00",
                    "X-Timestamp": "0",
                    "X-Forwarded-For": "203.0.113.66",
                },
            )
            assert junk.status_code == 401
        body = _event()
        genuine = await ac.post(
            "/api/v1/events/",
            content=body,
            headers={
                **_signed(body, sample_app.app_id),
                "X-Forwarded-For": "198.51.100.7",
            },
        )

    assert genuine.status_code == 200, genuine.text


async def test_signed_ingest_is_limited_per_app(limited: None, sample_app: App) -> None:
    async with _client() as ac:
        codes = []
        for i in range(
            LIMIT + 2
        ):  # each from its own IP, so only the app limit can bite
            body = _event()
            r = await ac.post(
                "/api/v1/events/",
                content=body,
                headers={
                    **_signed(body, sample_app.app_id),
                    "X-Forwarded-For": f"203.0.113.{i + 1}",
                },
            )
            codes.append(r.status_code)

    assert codes[:LIMIT] == [200] * LIMIT
    assert codes[LIMIT:] == [429, 429], codes


async def test_dsn_ingest_is_limited_per_app(limited: None, sample_app: App) -> None:
    auth = "Basic " + b64encode(f"{PUBLIC_ID}:".encode()).decode()
    async with _client() as ac:
        responses = []
        for i in range(LIMIT + 2):
            responses.append(
                await ac.post(
                    "/api/v1/events/public",
                    content=_event(),
                    headers={
                        "Authorization": auth,
                        "Content-Type": "application/json",
                        "X-Forwarded-For": f"203.0.113.{i + 1}",
                    },
                )
            )

    codes = [r.status_code for r in responses]
    assert codes[:LIMIT] == [200] * LIMIT
    assert codes[LIMIT:] == [429, 429], codes
    refused = responses[-1]
    assert refused.headers["Retry-After"]
    assert refused.headers["X-RateLimit-Limit"] == str(LIMIT)
