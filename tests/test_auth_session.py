"""Sign-in and session defences (FLEET-AUTH-SESSION-STANDARD; FLEET-BUILD-DEPLOY-STANDARD "Trusted
hosts"): a forged Host, a cross-site login POST, an open redirect, and a logout that only cleared
the cookie in one browser."""

import base64
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Self

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.auth as auth_module
from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.crud.dashboard_session_crud import (
    ensure_session_generation,
    reset_session_generation,
)
from app.models.app_model import App
from app.models.dashboard_session_model import DashboardSessionState

GENERATION = select(DashboardSessionState.generation)


async def _replay(client: AsyncClient, cookie: str) -> int:
    """GET a dashboard page with only `cookie` as the session."""
    client.cookies.set("analytics_session", cookie)
    r = await client.get("/dashboard/overview", follow_redirects=False)
    return r.status_code


CREDENTIALS = {
    "username": settings.DASHBOARD_USERNAME,
    "password": settings.DASHBOARD_PASSWORD,
}


async def _login(client: AsyncClient, ip: str, **data: str) -> str:
    """Sign in through the real route; the redirect it answered with."""
    client.base_url = "https://test"  # the session cookie is https_only
    r = await client.post(
        "/login",
        data={**CREDENTIALS, **data},
        headers={"X-Forwarded-For": ip},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return r.headers["location"]


# ── Trusted hosts ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.db
async def test_a_forged_host_is_refused(client: AsyncClient) -> None:
    r = await client.get("/login", headers={"Host": "attacker.example"})
    assert r.status_code == 400


@pytest.mark.db
async def test_a_served_host_is_answered_control(client: AsyncClient) -> None:
    r = await client.get("/login")
    assert r.status_code == 200


# ── Cross-site login (§5) ────────────────────────────────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://attacker.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
async def test_a_cross_site_login_post_is_refused(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    client.base_url = "https://test"
    r = await client.post(
        "/login",
        data=CREDENTIALS,
        headers={**headers, "X-Forwarded-For": "198.51.100.201"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert "analytics_session" not in r.cookies


@pytest.mark.db
async def test_a_same_origin_login_post_signs_in_control(client: AsyncClient) -> None:
    client.base_url = "https://test"
    r = await client.post(
        "/login",
        data=CREDENTIALS,
        headers={
            "Origin": "https://test",
            "Sec-Fetch-Site": "same-origin",
            "X-Forwarded-For": "198.51.100.202",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


# ── Redirects (§6) ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.parametrize(
    "target",
    [
        "//attacker.example",
        "https://attacker.example",
        "/\\attacker.example",
        "/%2F%2Fattacker.example",
    ],
)
async def test_login_never_redirects_off_site(client: AsyncClient, target: str) -> None:
    assert await _login(client, "198.51.100.203", next=target) == "/dashboard/overview"


@pytest.mark.db
async def test_login_keeps_a_same_site_next_control(client: AsyncClient) -> None:
    assert await _login(
        client, "198.51.100.204", next="/dashboard/events?hours=24"
    ) == ("/dashboard/events?hours=24")


@pytest.mark.db
async def test_a_bounced_request_keeps_its_whole_query(client: AsyncClient) -> None:
    """The login redirect carries the requested URL encoded, so its own `&` survives."""
    r = await client.get("/dashboard/events?hours=24&app_id=a", follow_redirects=False)
    assert r.status_code == 303
    assert (
        r.headers["location"]
        == "/login?next=/dashboard/events%3Fhours%3D24%26app_id%3Da"
    )


# ── Logout revokes on the server (§2.1) ──────────────────────────────────────────────────────────


@pytest.mark.db
async def test_a_copied_session_cookie_is_refused_after_logout(
    client: AsyncClient,
) -> None:
    await _login(client, "198.51.100.205")
    copied = client.cookies["analytics_session"]
    assert (await client.get("/dashboard/overview")).status_code == 200

    r = await client.post("/logout", follow_redirects=False)
    assert r.status_code == 303

    client.cookies.set("analytics_session", copied)
    after = await client.get("/dashboard/overview", follow_redirects=False)
    assert (
        after.status_code == 303
    )  # the copy is refused, not only this browser's cookie gone


@pytest.mark.db
async def test_a_logged_out_cookie_stays_refused_when_redis_is_restored(
    client: AsyncClient,
) -> None:
    """Redis keeps no AOF in prod and can come back from an older snapshot. The logout lives in the
    database, so whatever Redis holds after a restore, a copied cookie still in date stays
    refused."""
    await _login(client, "198.51.100.234")
    copied = client.cookies["analytics_session"]
    await client.post("/logout", follow_redirects=False)
    redis_client = await get_redis_client()
    assert redis_client is not None
    # What a snapshot from before that logout would hold
    await redis_client.flushdb()
    await redis_client.set(
        "dashboard:sessions_valid_after", "2000-01-01T00:00:00+00:00"
    )

    client.cookies.set("analytics_session", copied)
    after = await client.get("/dashboard/overview", follow_redirects=False)

    assert after.status_code == 303


@pytest.mark.db
async def test_a_missing_row_refuses_every_session(
    client: AsyncClient, db: AsyncSession
) -> None:
    """With the row gone, no cookie can be checked against the last logout: none is accepted, and
    no new one is opened."""
    await _login(client, "198.51.100.235")
    copied = client.cookies["analytics_session"]
    await db.execute(delete(DashboardSessionState))

    assert await _replay(client, copied) == 303
    assert "session+store+is+down" in await _login(client, "198.51.100.235")


@pytest.mark.db
async def test_startup_rewrites_a_lost_row_signing_everyone_out(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _login(client, "198.51.100.237")
    copied = client.cookies["analytics_session"]
    await db.execute(delete(DashboardSessionState))

    await ensure_session_generation()

    assert await _replay(client, copied) == 303
    client.cookies.clear()
    await _login(client, "198.51.100.237")
    assert (await client.get("/dashboard/overview")).status_code == 200


@pytest.mark.db
async def test_reading_the_generation_writes_nothing(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    """Every signed-in GET reads the generation: a GET that wrote would be reachable cross-site."""
    before = (await db.execute(GENERATION)).scalar_one()

    assert (await dashboard_client.get("/dashboard/overview")).status_code == 200
    assert (await db.execute(GENERATION)).scalar_one() == before


@pytest.mark.db
async def test_a_logout_after_the_clock_steps_back_still_revokes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clock ran fast at sign-in and NTP stepped it back before the logout: a count, not a
    time, so the copy is refused all the same."""
    await _login(client, "198.51.100.238")
    copied = client.cookies["analytics_session"]
    stepped_back = datetime.now(UTC) - timedelta(minutes=2)

    class SteppedBack(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(stepped_back.timestamp(), tz)

    monkeypatch.setattr(auth_module, "datetime", SteppedBack)
    await client.post("/logout", follow_redirects=False)
    monkeypatch.undo()

    assert await _replay(client, copied) == 303


@pytest.mark.db
async def test_two_logouts_both_count(client: AsyncClient, db: AsyncSession) -> None:
    """A logout advances the generation by one atomically: two logouts in any order are two steps,
    so neither can undo the other."""
    before = (await db.execute(GENERATION)).scalar_one()
    for ip in ("198.51.100.239", "198.51.100.241"):
        await _login(client, ip)
        await client.post("/logout", follow_redirects=False)

    assert (await db.execute(GENERATION)).scalar_one() == before + 2


@pytest.mark.db
async def test_signing_everyone_out_after_a_restore_refuses_every_cookie(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A restored dump brings back the generation of its time, which a cookie revoked later may
    hold; `make restore` then moves to a fresh generation."""
    dump_time = (await db.execute(GENERATION)).scalar_one()
    await _login(client, "198.51.100.242")
    revoked_later = client.cookies["analytics_session"]
    await client.post("/logout", follow_redirects=False)
    await db.execute(update(DashboardSessionState).values(generation=dump_time))
    assert (
        await _replay(client, revoked_later) == 200
    )  # what the restore alone brings back

    await reset_session_generation()  # what app.scripts.sign_out_everyone runs

    assert await _replay(client, revoked_later) == 303


@pytest.mark.db
async def test_dashboard_sessions_work_without_redis(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sessions need only the database: Redis down costs the rate limits their shared window, not
    the dashboard."""
    monkeypatch.setattr(settings, "REDIS_URL", None)
    await _login(client, "198.51.100.236")

    assert (await client.get("/dashboard/overview")).status_code == 200
    out = await client.post("/logout", follow_redirects=False)
    assert out.headers["location"].startswith("/login?message=")


@pytest.mark.db
async def test_a_new_login_after_logout_works_control(client: AsyncClient) -> None:
    await _login(client, "198.51.100.206")
    await client.post("/logout", follow_redirects=False)
    await _login(client, "198.51.100.206")
    assert (await client.get("/dashboard/overview")).status_code == 200


@pytest.mark.db
async def test_logout_is_not_a_get(client: AsyncClient) -> None:
    """A link on another site cannot sign the operator out."""
    assert (await client.get("/logout")).status_code == 405


@pytest.mark.db
async def test_an_anonymous_logout_revokes_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    before = (await db.execute(GENERATION)).scalar_one()
    r = await client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert (await db.execute(GENERATION)).scalar_one() == before


async def _database_down() -> None:
    raise OperationalError("SELECT 1", {}, ConnectionRefusedError())


@pytest.mark.db
async def test_a_session_is_refused_when_logouts_cannot_be_checked(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _login(client, "198.51.100.207")

    monkeypatch.setattr(auth_module, "read_session_generation", _database_down)
    r = await client.get("/dashboard/overview", follow_redirects=False)
    assert r.status_code == 303


# ── Adversarial pass findings (kept as regressions) ─────────────────────────────────────────────


@pytest.mark.db
async def test_sdk_ingest_from_a_webview_origin_is_not_refused(
    client: AsyncClient, sample_app: App
) -> None:
    """The ingest API authenticates by DSN, never a cookie: a WebView client (prod CORS_ORIGINS
    lists capacitor://app) posts with its own Origin and must not meet the login-CSRF guard."""
    token = base64.b64encode(f"{sample_app.public_id}:".encode()).decode()
    r = await client.post(
        "/api/v1/events/public",
        json={"name": "app_opened", "timestamp": "2026-10-06T12:00:00Z"},
        headers={"Authorization": f"Basic {token}", "Origin": "capacitor://app"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.db
async def test_a_logout_during_a_database_outage_says_it_revoked_nothing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A logout whose stamp cannot be written says so: a copied cookie would still work once the
    database is back."""
    await _login(client, "198.51.100.208")
    monkeypatch.setattr(auth_module, "read_session_generation", _database_down)
    monkeypatch.setattr(auth_module, "advance_session_generation", _database_down)
    r = await client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert "other+sessions+were+not+revoked" in r.headers["location"]
    assert "analytics_session" not in client.cookies  # this browser is signed out


@pytest.mark.db
async def test_sign_in_says_so_when_the_session_store_is_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A password accepted while sessions cannot be checked would loop back to the login page."""
    monkeypatch.setattr(auth_module, "read_session_generation", _database_down)
    client.base_url = "https://test"
    r = await client.post(
        "/login",
        data=CREDENTIALS,
        headers={"X-Forwarded-For": "198.51.100.209"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "session+store+is+down" in r.headers["location"]


@pytest.mark.db
async def test_the_app_switcher_never_redirects_off_site(client: AsyncClient) -> None:
    await _login(client, "198.51.100.211")
    r = await client.post(
        "/set-app-context",
        data={"app_id": ""},
        headers={"HX-Current-URL": "https://test//attacker.example/x"},
    )
    assert r.status_code == 204
    assert r.headers["hx-redirect"] == "/dashboard/overview"
