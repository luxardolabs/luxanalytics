"""Sign-in and session defences (FLEET-AUTH-SESSION-STANDARD; FLEET-BUILD-DEPLOY-STANDARD "Trusted
hosts"): a forged Host, a cross-site login POST, an open redirect, and a logout that only cleared
the cookie in one browser."""

import base64
from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.auth as auth_module
from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.models.app_model import App
from app.models.dashboard_session_model import DashboardSessionState

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
async def test_with_no_logout_recorded_sign_in_works_and_logout_records_one(
    client: AsyncClient, db: AsyncSession
) -> None:
    """An emptied table records no logout: sign-in works, and the next logout writes the row."""
    await db.execute(delete(DashboardSessionState))

    await _login(client, "198.51.100.235")
    assert (await client.get("/dashboard/overview")).status_code == 200
    copied = client.cookies["analytics_session"]
    await client.post("/logout", follow_redirects=False)

    client.cookies.set("analytics_session", copied)
    after = await client.get("/dashboard/overview", follow_redirects=False)
    assert after.status_code == 303


@pytest.mark.db
async def test_reading_the_stamp_writes_nothing(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    """Every signed-in GET reads the stamp: a GET that wrote would be reachable cross-site."""
    stamp = select(DashboardSessionState.sessions_valid_after)
    before = (await db.execute(stamp)).scalar_one_or_none()

    assert (await dashboard_client.get("/dashboard/overview")).status_code == 200
    assert (await db.execute(stamp)).scalar_one_or_none() == before


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
    stamp = select(DashboardSessionState.sessions_valid_after)
    before = (await db.execute(stamp)).scalar_one_or_none()
    r = await client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert (await db.execute(stamp)).scalar_one_or_none() == before


async def _database_down(*now: datetime) -> None:
    raise OperationalError("SELECT 1", {}, ConnectionRefusedError())


@pytest.mark.db
async def test_a_session_is_refused_when_logouts_cannot_be_checked(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _login(client, "198.51.100.207")

    monkeypatch.setattr(auth_module, "read_sessions_valid_after", _database_down)
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
    monkeypatch.setattr(auth_module, "read_sessions_valid_after", _database_down)
    monkeypatch.setattr(auth_module, "write_sessions_valid_after", _database_down)
    r = await client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert "other+sessions+were+not+revoked" in r.headers["location"]
    assert "analytics_session" not in client.cookies  # this browser is signed out


@pytest.mark.db
async def test_sign_in_says_so_when_the_session_store_is_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A password accepted while sessions cannot be checked would loop back to the login page."""
    monkeypatch.setattr(auth_module, "read_sessions_valid_after", _database_down)
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
