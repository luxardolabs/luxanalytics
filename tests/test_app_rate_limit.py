"""Ingest is limited in events, per client address, per device and per app, after authentication,
on HMAC and DSN alike, spent all or nothing.

An ingest credential (the DSN's public_id, an HMAC key id) ships in the app binary, so a budget
keyed on the app alone is one anyone holding it can spend. The device limit is the tight one: a
flooding device is refused without costing the app's other devices. Device ids are client-chosen,
so the address limit bounds one client under any number of them, and the app ceiling, above one
address's worth, bounds a flood spread over many. Nothing that fails authentication or does not
parse spends a budget, and neither does a refused batch. The signed stats read keeps a per-app
request limit.
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
from app.core.config import Settings, settings
from app.main import create_application
from app.models.app_model import App
from tests.test_ingest_http import PUBLIC_ID
from tests.test_settings import REQUIRED, SAFE

pytestmark = pytest.mark.db

LIMIT = 5
IP = "192.0.2.40"


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
        await rate_limiter.enforce_ingest_limits("app_one", IP, {"shared": 1})
    with pytest.raises(rate_limiter.AppRateLimitExceeded):
        await rate_limiter.enforce_ingest_limits("app_one", IP, {"shared": 1})

    await rate_limiter.enforce_ingest_limits("app_two", IP, {"shared": 1})


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


async def test_the_in_memory_fallback_stays_bounded_under_new_identifiers(
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


def _batch_of(devices: dict[str | None, int], ids: list[str] | None = None) -> bytes:
    """A batch with `devices[d]` events from each device; `ids` gives the events' wire ids."""
    events = [_one(device) for device, count in devices.items() for _ in range(count)]
    for event, event_id in zip(events, ids or [], strict=False):
        event["id"] = event_id
    return json.dumps({"events": events}).encode()


async def test_one_address_cannot_fill_the_app_ceiling(
    monkeypatch: pytest.MonkeyPatch, sample_app: App
) -> None:
    """Device ids are client-chosen: one address sending each event under a new one is held by
    its address budget, below the app's ceiling, and an honest device elsewhere still sends."""
    monkeypatch.setattr(settings, "IP_RATE_LIMIT_EVENTS", 20)
    monkeypatch.setattr(settings, "APP_RATE_LIMIT_EVENTS", 50)
    async with _client() as ac:
        flood = []
        for r in range(5):
            r_batch = _batch_of({f"fake-{r}-{i}": 1 for i in range(10)})
            flood.append(
                await _post_dsn(ac, r_batch, sample_app.project_id, "203.0.113.66")
            )
        honest = await _post_dsn(
            ac, _event("honest-phone"), sample_app.project_id, "198.51.100.7"
        )

    assert flood == [200, 200, 429, 429, 429]
    assert honest == 200


async def test_a_refused_batch_spends_no_device_budget(
    device_limit: None, sample_app: App
) -> None:
    async with _client() as ac:
        refused = await _post_dsn(
            ac, _batch_of({"A": 3, "Z": 6}), sample_app.project_id, "203.0.113.1"
        )
        a_again = await _post_dsn(
            ac, _batch_of({"A": LIMIT}), sample_app.project_id, "203.0.113.2"
        )

    assert (refused, a_again) == (429, 200)


async def test_a_ceiling_refusal_spends_no_device_budget(
    app_ceiling: None, device_limit: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    await rate_limiter.enforce_ingest_limits("app_x", IP, {"B": LIMIT})
    clock[0] += 30
    with pytest.raises(rate_limiter.AppRateLimitExceeded) as refused:
        await rate_limiter.enforce_ingest_limits("app_x", "192.0.2.41", {"A": LIMIT})
    assert refused.value.error == "App rate limit exceeded"

    clock[0] += 31  # B's events have left the window; A stored nothing
    await rate_limiter.enforce_ingest_limits("app_x", "192.0.2.41", {"A": LIMIT})


@pytest.mark.parametrize("redis_up", [True, False])
async def test_waiting_retry_after_admits_a_whole_batch(
    device_limit: None, monkeypatch: pytest.MonkeyPatch, redis_up: bool
) -> None:
    """Retry-After is when enough events have left for the refused batch, not just one."""
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    if not redis_up:
        monkeypatch.setattr(rate_limiter, "get_redis_client", _no_redis)
        monkeypatch.setattr(
            rate_limiter,
            "datetime",
            type(
                "Clock",
                (),
                {"now": staticmethod(lambda tz: datetime.fromtimestamp(clock[0], tz))},
            ),
        )
    app_id = f"app_retry_{redis_up}"
    await rate_limiter.enforce_ingest_limits(app_id, IP, {"A": 1})
    clock[0] += 20
    await rate_limiter.enforce_ingest_limits(app_id, IP, {"A": LIMIT - 1})
    clock[0] += 0.5
    with pytest.raises(rate_limiter.AppRateLimitExceeded) as refused:
        await rate_limiter.enforce_ingest_limits(app_id, IP, {"A": 3})

    clock[0] += refused.value.metadata["retry_after"]
    await rate_limiter.enforce_ingest_limits(app_id, IP, {"A": 3})


async def test_the_in_memory_retry_after_is_at_least_a_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truncated to 0, the 429 would fall back to a whole minute."""
    monkeypatch.setattr(rate_limiter, "get_redis_client", _no_redis)
    limiter = rate_limiter.RateLimiter(1, 1)
    assert (await limiter.is_allowed("x"))[0]

    allowed, metadata = await limiter.is_allowed("x")

    assert not allowed
    assert metadata["retry_after"] >= 1


async def test_the_in_memory_fallback_bounds_live_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New device ids all still in their window: the fallback keeps at most its bound, and the
    cost of each new one stays flat past it (no scan per insert)."""
    monkeypatch.setattr(rate_limiter, "get_redis_client", _no_redis)
    limiter = rate_limiter.RateLimiter(10, 60, memory_identifiers_max=1000)

    async def held_after(count: int, prefix: str) -> tuple[int, float]:
        tracemalloc.start()
        try:
            before = tracemalloc.take_snapshot()
            started = time.perf_counter()
            for i in range(count):
                assert (await limiter.is_allowed(f"{prefix}-{i}"))[0]
            elapsed = time.perf_counter() - started
            after = tracemalloc.take_snapshot()
        finally:
            tracemalloc.stop()
        held = sum(
            stat.size_diff
            for stat in after.compare_to(before, "filename")
            if stat.traceback[0].filename == rate_limiter.__file__
        )
        return held, elapsed

    held_below, below = await held_after(1000, "below")
    held_above, above = await held_after(5000, "above")

    assert held_above < held_below * 2, (held_below, held_above)
    assert above < below * 5 * 10, (below, above)


async def test_a_repeated_event_id_in_a_batch_is_counted_once(
    device_limit: None, sample_app: App
) -> None:
    """The batch stores each id once, so its copies do not cost the device's budget."""
    ids = ["e1", "e1", "e1", "e2", "e2", "e2"]
    async with _client() as ac:
        r = await ac.post(
            f"/api/v1/events/{sample_app.project_id}",
            content=_batch_of({"A": len(ids)}, ids),
            headers={**_dsn(PUBLIC_ID), "X-Forwarded-For": "203.0.113.1"},
        )

    assert r.status_code == 200, r.text
    assert r.json()["duplicates"] == 4


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"DEVICE_RATE_LIMIT_EVENTS": 999}, "DEVICE_RATE_LIMIT_EVENTS"),
        ({"IP_RATE_LIMIT_EVENTS": 999}, "IP_RATE_LIMIT_EVENTS"),
        (
            {"IP_RATE_LIMIT_EVENTS": 6000, "APP_RATE_LIMIT_EVENTS": 6000},
            "APP_RATE_LIMIT_EVENTS",
        ),
    ],
)
def test_ingest_limits_that_cannot_work_refuse_to_start(
    overrides: dict[str, int], message: str
) -> None:
    """A budget below one batch refuses it on every retry; a ceiling one address can fill is a
    budget anyone holding the DSN can spend."""
    with pytest.raises(ValueError, match=message):
        Settings.model_validate({**REQUIRED, **SAFE, **overrides})


def _clock(monkeypatch: pytest.MonkeyPatch, redis_up: bool) -> list[float]:
    """A settable clock for both windows: Redis's (time.time) and the fallback's (datetime)."""
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    if not redis_up:
        monkeypatch.setattr(rate_limiter, "get_redis_client", _no_redis)
        monkeypatch.setattr(
            rate_limiter,
            "datetime",
            type(
                "Clock",
                (),
                {"now": staticmethod(lambda tz: datetime.fromtimestamp(clock[0], tz))},
            ),
        )
    return clock


@pytest.mark.parametrize("redis_up", [True, False])
async def test_waiting_retry_after_admits_when_two_budgets_are_full(
    monkeypatch: pytest.MonkeyPatch, redis_up: bool
) -> None:
    """The address is full until t+60 and the device until t+90: Retry-After is the later one."""
    monkeypatch.setattr(settings, "IP_RATE_LIMIT_EVENTS", LIMIT)
    monkeypatch.setattr(settings, "DEVICE_RATE_LIMIT_EVENTS", LIMIT)
    clock = _clock(monkeypatch, redis_up)
    app_id = f"app_two_full_{redis_up}"
    address, other = f"198.18.{int(redis_up)}.10", f"198.18.{int(redis_up)}.20"
    await rate_limiter.enforce_ingest_limits(app_id, address, {"B": LIMIT})
    clock[0] += 30
    await rate_limiter.enforce_ingest_limits(app_id, other, {"A": LIMIT})
    clock[0] += 1
    with pytest.raises(rate_limiter.AppRateLimitExceeded) as refused:
        await rate_limiter.enforce_ingest_limits(app_id, address, {"A": 1})
    assert refused.value.error == "Device rate limit exceeded"

    clock[0] += refused.value.metadata["retry_after"]
    await rate_limiter.enforce_ingest_limits(app_id, address, {"A": 1})


@pytest.mark.parametrize(
    "addresses",
    [
        ["203.0.113.66"] * 3,
        [f"2001:db8:66:1::{i + 1:x}" for i in range(3)],
        ["203.0.113.67", "::ffff:203.0.113.67", "::ffff:203.0.113.67"],
    ],
    ids=["one-ipv4", "one-ipv6-64", "ipv4-mapped"],
)
async def test_one_host_cannot_fill_the_app_ceiling_over_its_addresses(
    monkeypatch: pytest.MonkeyPatch, sample_app: App, addresses: list[str]
) -> None:
    """An IPv6 host holds its whole /64, and an IPv4 client may also arrive IPv4-mapped: each is
    one address budget."""
    monkeypatch.setattr(settings, "IP_RATE_LIMIT_EVENTS", 20)
    monkeypatch.setattr(settings, "APP_RATE_LIMIT_EVENTS", 30)
    async with _client() as ac:
        for a, address in enumerate(addresses):
            for r in range(2):
                body = _batch_of({f"fake-{a}-{r}-{i}": 1 for i in range(10)})
                await _post_dsn(ac, body, sample_app.project_id, address)
        honest = await _post_dsn(
            ac, _event("honest-phone"), sample_app.project_id, "198.51.100.7"
        )

    assert honest == 200


@pytest.mark.parametrize("window", [0, -1])
def test_an_ingest_window_that_cannot_work_refuses_to_start(window: int) -> None:
    """A window under a second empties every budget on each check: no limit at all."""
    with pytest.raises(ValueError, match="INGEST_RATE_LIMIT_WINDOW"):
        Settings.model_validate(
            {**REQUIRED, **SAFE, "INGEST_RATE_LIMIT_WINDOW": window}
        )
