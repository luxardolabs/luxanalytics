"""The per-app ingest rate limit counts only requests that proved they are the app.

It was keyed on the unverified X-Key-ID header and counted before authentication, so anyone could
spend an app's budget with junk requests carrying its (public) key id and the app's own signed
events got 429 (adversarial audit 2026-10-27, LUXANALYTI-97). DSN ingest is not limited per app:
its credential (the public_id) ships in every app binary, so a per-app DSN budget is one anyone can
spend, and the SDK drops a batch on a 429. The IP limit covers it.
"""

import hashlib
import hmac
import json
import time
from base64 import b64encode
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

import app.core.rate_limiter as rate_limiter
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


def _dsn(public_id: str) -> dict[str, str]:
    return {
        "Authorization": "Basic " + b64encode(f"{public_id}:".encode()).decode(),
        "Content-Type": "application/json",
    }


async def test_public_dsn_requests_cannot_starve_the_app(
    limited: None, sample_app: App
) -> None:
    """The DSN is public: junk sent with it from one IP must not cost the app's signed or DSN
    ingest anything."""
    async with _client() as ac:
        for _ in range(LIMIT):
            junk = await ac.post(
                f"/api/v1/events/{sample_app.project_id}",
                content=b"not json",
                headers={**_dsn(PUBLIC_ID), "X-Forwarded-For": "203.0.113.66"},
            )
            assert junk.status_code == 400
        body = _event()
        signed = await ac.post(
            "/api/v1/events/",
            content=body,
            headers={
                **_signed(body, sample_app.app_id),
                "X-Forwarded-For": "198.51.100.7",
            },
        )
        device = await ac.post(
            f"/api/v1/events/{sample_app.project_id}",
            content=_event(),
            headers={**_dsn(PUBLIC_ID), "X-Forwarded-For": "198.51.100.8"},
        )

    assert (signed.status_code, device.status_code) == (200, 200)


async def _no_redis() -> None:
    return None


async def test_the_app_limit_holds_without_redis(
    limited: None, sample_app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis down: the per-app limit falls back to the process's in-memory window, as the IP limit
    does, instead of switching off."""
    monkeypatch.setattr(rate_limiter, "get_redis_client", _no_redis)
    app_id = sample_app.app_id
    async with _client() as ac:
        codes = []
        for i in range(LIMIT + 2):
            body = _event()
            r = await ac.post(
                "/api/v1/events/",
                content=body,
                headers={
                    **_signed(body, app_id),
                    "X-Forwarded-For": f"203.0.113.{i + 100}",
                },
            )
            codes.append(r.status_code)

    assert codes[LIMIT:] == [429, 429], codes


async def test_signed_stats_reads_are_limited_per_app(
    limited: None, sample_app: App
) -> None:
    async with _client() as ac:
        codes = []
        for i in range(LIMIT + 1):
            r = await ac.get(
                "/api/v1/events/stats",
                headers={
                    **_signed(b"", sample_app.app_id),
                    "X-Forwarded-For": f"203.0.113.{i + 1}",
                },
            )
            codes.append(r.status_code)

    assert codes[:LIMIT] == [200] * LIMIT
    assert codes[LIMIT] == 429, codes


async def test_both_rate_limits_answer_the_same_429(
    limited: None, sample_app: App
) -> None:
    async with _client() as ac:
        for i in range(LIMIT):  # the app's budget, spent from distinct IPs
            body = _event()
            await ac.post(
                "/api/v1/events/",
                content=body,
                headers={
                    **_signed(body, sample_app.app_id),
                    "X-Forwarded-For": f"203.0.113.{i + 1}",
                },
            )
        body = _event()
        app_429 = await ac.post(
            "/api/v1/events/",
            content=body,
            headers={
                **_signed(body, sample_app.app_id),
                "X-Forwarded-For": "203.0.113.200",
            },
        )
        for _ in range(LIMIT):  # one IP's budget
            await ac.get("/login", headers={"X-Forwarded-For": "198.51.100.250"})
        ip_429 = await ac.get("/login", headers={"X-Forwarded-For": "198.51.100.250"})

    assert (app_429.status_code, ip_429.status_code) == (429, 429)
    assert set(app_429.json()) == set(ip_429.json()) == {"error", "retry_after"}
    for r in (app_429, ip_429):
        assert r.headers["Retry-After"]
        assert r.headers["X-RateLimit-Limit"] == str(LIMIT)
