"""Ingest is limited in events, per device and per app, after authentication, on HMAC and DSN alike.

An ingest credential (the DSN's public_id, an HMAC key id) ships in the app binary, so a budget
keyed on the app alone is one anyone holding it can spend. The device limit is the tight one: a
flooding device is refused without costing the app's other devices. The app ceiling sits far above
normal traffic and bounds a flood spread over many device ids. Nothing that fails authentication or
does not parse spends either budget. The signed stats read keeps a per-app request limit.
"""

import hashlib
import hmac
import json
import time
import tracemalloc
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
def app_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app's ceiling at LIMIT events per window, so a test can reach it."""
    monkeypatch.setattr(settings, "APP_RATE_LIMIT_EVENTS", LIMIT)


@pytest.fixture
def device_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each device's limit at LIMIT events per window; the app's ceiling out of reach."""
    monkeypatch.setattr(settings, "DEVICE_RATE_LIMIT_EVENTS", LIMIT)


@pytest.fixture
def request_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The request limits (per IP, and the stats read's per app) at LIMIT per window."""
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", LIMIT)


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_application()), base_url="https://localhost"
    )


def _one(device_id: str | None) -> dict[str, object]:
    metadata = {} if device_id is None else {"device_id": device_id}
    return {
        "name": "app_opened",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": metadata,
    }


def _event(device_id: str | None = "device-1") -> bytes:
    return json.dumps(_one(device_id)).encode()


def _batch(count: int, device_id: str | None = "device-1") -> bytes:
    return json.dumps({"events": [_one(device_id) for _ in range(count)]}).encode()


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


def _dsn(public_id: str) -> dict[str, str]:
    return {
        "Authorization": "Basic " + b64encode(f"{public_id}:".encode()).decode(),
        "Content-Type": "application/json",
    }


async def _post_signed(ac: AsyncClient, body: bytes, app_id: str, ip: str) -> int:
    r = await ac.post(
        "/api/v1/events/",
        content=body,
        headers={**_signed(body, app_id), "X-Forwarded-For": ip},
    )
    return r.status_code


async def _post_dsn(ac: AsyncClient, body: bytes, project_id: str, ip: str) -> int:
    r = await ac.post(
        f"/api/v1/events/{project_id}",
        content=body,
        headers={**_dsn(PUBLIC_ID), "X-Forwarded-For": ip},
    )
    return r.status_code


async def test_unauthenticated_requests_cannot_spend_an_apps_budget(
    app_ceiling: None, sample_app: App
) -> None:
    async with _client() as ac:
        # One IP with no secret sends junk carrying the app's key id, past the ceiling.
        for _ in range(LIMIT + 1):
            junk = await ac.post(
                "/api/v1/events/",
                content=_event(),
                headers={
                    "Content-Type": "application/json",
                    "X-Key-ID": sample_app.app_id,
                    "X-HMAC-Signature": "00",
                    "X-Timestamp": "0",
                    "X-Forwarded-For": "203.0.113.66",
                },
            )
            assert junk.status_code == 401
        genuine = await _post_signed(ac, _event(), sample_app.app_id, "198.51.100.7")

    assert genuine == 200


async def test_public_dsn_junk_cannot_spend_an_apps_budget(
    app_ceiling: None, sample_app: App
) -> None:
    """A body that does not parse is refused before it is counted: the DSN is public, so junk sent
    with it must not cost the app's signed or DSN ingest anything."""
    async with _client() as ac:
        for _ in range(LIMIT + 1):
            junk = await ac.post(
                f"/api/v1/events/{sample_app.project_id}",
                content=b"not json",
                headers={**_dsn(PUBLIC_ID), "X-Forwarded-For": "203.0.113.66"},
            )
            assert junk.status_code == 400
        signed = await _post_signed(ac, _event(), sample_app.app_id, "198.51.100.7")
        device = await _post_dsn(ac, _event(), sample_app.project_id, "198.51.100.8")

    assert (signed, device) == (200, 200)


async def test_signed_ingest_is_held_to_the_app_ceiling(
    app_ceiling: None, sample_app: App
) -> None:
    async with _client() as ac:
        # Each from its own device and IP, so only the app ceiling can bite.
        codes = [
            await _post_signed(
                ac, _event(f"device-{i}"), sample_app.app_id, f"203.0.113.{i + 1}"
            )
            for i in range(LIMIT + 2)
        ]

    assert codes == [200] * LIMIT + [429, 429]


async def test_dsn_ingest_is_held_to_the_app_ceiling(
    app_ceiling: None, sample_app: App
) -> None:
    async with _client() as ac:
        codes = [
            await _post_dsn(
                ac,
                _event(f"device-{i}"),
                sample_app.project_id,
                f"203.0.113.{i + 1}",
            )
            for i in range(LIMIT + 2)
        ]

    assert codes == [200] * LIMIT + [429, 429]


async def test_hmac_and_dsn_share_one_app_ceiling(
    app_ceiling: None, sample_app: App
) -> None:
    async with _client() as ac:
        for i in range(LIMIT):
            assert (
                await _post_dsn(
                    ac,
                    _event(f"device-{i}"),
                    sample_app.project_id,
                    f"203.0.113.{i + 1}",
                )
                == 200
            )
        signed = await _post_signed(
            ac, _event("device-other"), sample_app.app_id, "203.0.113.99"
        )

    assert signed == 429


async def test_a_flooding_device_is_refused_without_costing_the_others(
    device_limit: None, sample_app: App
) -> None:
    async with _client() as ac:
        flood = [
            await _post_dsn(
                ac, _event("flooder"), sample_app.project_id, f"203.0.113.{i + 1}"
            )
            for i in range(LIMIT + 1)
        ]
        refused = await ac.post(
            f"/api/v1/events/{sample_app.project_id}",
            content=_event("flooder"),
            headers={**_dsn(PUBLIC_ID), "X-Forwarded-For": "203.0.113.50"},
        )
        other_dsn = await _post_dsn(
            ac, _event("another"), sample_app.project_id, "198.51.100.8"
        )
        other_signed = await _post_signed(
            ac, _event("a-third"), sample_app.app_id, "198.51.100.9"
        )

    assert flood == [200] * LIMIT + [429]
    assert refused.status_code == 429
    assert refused.json()["error"] == "Device rate limit exceeded"
    assert refused.headers["X-RateLimit-Limit"] == str(LIMIT)
    assert (other_dsn, other_signed) == (200, 200)


async def test_a_device_is_limited_in_events_not_requests(
    device_limit: None, sample_app: App
) -> None:
    async with _client() as ac:
        full = await _post_dsn(ac, _batch(LIMIT), sample_app.project_id, "203.0.113.1")
        one_more = await _post_dsn(ac, _event(), sample_app.project_id, "203.0.113.2")
        too_big = await _post_dsn(
            ac, _batch(LIMIT + 1, "device-2"), sample_app.project_id, "203.0.113.3"
        )

    assert (full, one_more, too_big) == (200, 429, 429)


async def test_events_without_a_device_id_share_one_budget(
    device_limit: None, sample_app: App
) -> None:
    async with _client() as ac:
        codes = [
            await _post_dsn(
                ac, _event(None), sample_app.project_id, f"203.0.113.{i + 1}"
            )
            for i in range(LIMIT + 1)
        ]
        with_id = await _post_dsn(
            ac, _event("device-1"), sample_app.project_id, "198.51.100.8"
        )

    assert codes == [200] * LIMIT + [429]
    assert with_id == 200


async def test_device_budgets_are_per_app(device_limit: None) -> None:
    """The same device id under two apps is two budgets: the SDK's device id is shared by every
    app of one vendor on a phone."""
    for _ in range(LIMIT):
        await rate_limiter.enforce_ingest_limits("app_one", {"shared": 1})
    with pytest.raises(rate_limiter.AppRateLimitExceeded):
        await rate_limiter.enforce_ingest_limits("app_one", {"shared": 1})

    await rate_limiter.enforce_ingest_limits("app_two", {"shared": 1})


async def _no_redis() -> None:
    return None


async def test_the_ingest_limits_hold_without_redis(
    app_ceiling: None, sample_app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis down: the ingest limits fall back to the process's in-memory window, as the IP limit
    does, instead of switching off."""
    monkeypatch.setattr(rate_limiter, "get_redis_client", _no_redis)
    async with _client() as ac:
        codes = [
            await _post_signed(
                ac, _event(f"device-{i}"), sample_app.app_id, f"203.0.113.{i + 100}"
            )
            for i in range(LIMIT + 2)
        ]

    assert codes[LIMIT:] == [429, 429], codes


async def test_the_in_memory_fallback_drops_identifiers_past_their_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Device ids are client-chosen: with Redis down, a flood of new ones, each seen once, must
    not grow the fallback without bound. Measured as what the limiter's own code keeps allocated
    (unbounded, 5000 identifiers hold about 1 MB there)."""
    monkeypatch.setattr(rate_limiter, "get_redis_client", _no_redis)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(
        rate_limiter,
        "datetime",
        type(
            "Clock",
            (),
            {"now": staticmethod(lambda tz: datetime.fromtimestamp(clock[0], tz))},
        ),
    )
    limiter = rate_limiter.RateLimiter(10, 60, memory_identifiers_max=50)
    devices = [f"device-{i}" for i in range(5000)]

    tracemalloc.start()
    try:
        before = tracemalloc.take_snapshot()
        for device in devices:
            clock[0] += 61  # each one's window has passed by the next
            assert (await limiter.is_allowed(device))[0]
        after = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()

    held = sum(
        stat.size_diff
        for stat in after.compare_to(before, "filename")
        if stat.traceback[0].filename == rate_limiter.__file__
    )
    assert held < 100_000, held


async def test_signed_stats_reads_are_limited_per_app(
    request_limit: None, sample_app: App
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


async def test_every_rate_limit_answers_the_same_429(
    app_ceiling: None, request_limit: None, sample_app: App
) -> None:
    async with _client() as ac:
        for i in range(LIMIT):  # the app's ceiling, spent from distinct devices and IPs
            await _post_signed(
                ac, _event(f"device-{i}"), sample_app.app_id, f"203.0.113.{i + 1}"
            )
        body = _event("device-last")
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


async def test_the_app_429_reports_the_app_window(
    app_ceiling: None, sample_app: App
) -> None:
    """The middleware adds the IP window's X-RateLimit-* to responses it passes: never onto a 429
    the app ceiling answered, which says 0 remaining."""
    async with _client() as ac:
        for i in range(LIMIT):
            await _post_signed(
                ac, _event(f"device-{i}"), sample_app.app_id, f"203.0.113.{i + 1}"
            )
        body = _event("device-last")
        refused = await ac.post(
            "/api/v1/events/",
            content=body,
            headers={
                **_signed(body, sample_app.app_id),
                "X-Forwarded-For": "203.0.113.201",
            },
        )

    assert refused.status_code == 429
    assert refused.headers["X-RateLimit-Remaining"] == "0"


async def test_a_per_app_limit_does_not_change_other_apps_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-app budget applies to that app only; the shared limiter is not reconfigured by it."""
    limiter = rate_limiter.AppRateLimiter()
    monkeypatch.setattr(rate_limiter, "APP_LIMITS", {"small_app": (1, 60)})
    monkeypatch.setattr(rate_limiter, "get_redis_client", _no_redis)

    assert (await limiter.check_app_limit("small_app"))[0]
    assert not (await limiter.check_app_limit("small_app"))[0]
    for _ in range(3):
        allowed, _meta = await limiter.check_app_limit("other_app")
        assert allowed
    assert limiter.requests_per_window == settings.RATE_LIMIT_REQUESTS
