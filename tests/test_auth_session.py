"""Sign-in and session defences (FLEET-AUTH-SESSION-STANDARD; FLEET-BUILD-DEPLOY-STANDARD "Trusted
hosts"): a forged Host, a cross-site login POST, an open redirect, and a logout that only cleared
the cookie in one browser."""

import base64

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.auth as auth_module
from app.core.auth import SESSIONS_VALID_AFTER_KEY
from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.main import create_application
from app.models.app_model import App

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
async def test_an_anonymous_logout_revokes_nothing(client: AsyncClient) -> None:
    r = await client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    redis_client = await get_redis_client()
    assert redis_client is not None
    assert await redis_client.get(SESSIONS_VALID_AFTER_KEY) is None


@pytest.mark.db
async def test_a_session_is_refused_when_logouts_cannot_be_checked(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _login(client, "198.51.100.207")

    async def no_redis() -> None:
        return None

    monkeypatch.setattr(auth_module, "get_redis_client", no_redis)
    r = await client.get("/dashboard/overview", follow_redirects=False)
    assert r.status_code == 303


# ── Adversarial pass findings (kept as regressions) ─────────────────────────────────────────────


async def _no_redis() -> None:
    return None


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
async def test_a_logout_during_a_redis_outage_says_it_revoked_nothing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to answer "You have been logged out" and skip the stamp, so a copied cookie
    still worked once Redis was back."""
    await _login(client, "198.51.100.208")
    monkeypatch.setattr(auth_module, "get_redis_client", _no_redis)
    r = await client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert "other+sessions+were+not+revoked" in r.headers["location"]
    assert "analytics_session" not in client.cookies  # this browser is signed out


@pytest.mark.db
async def test_sign_in_says_so_when_the_session_store_is_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to accept the password and loop back to the login page with no word why."""
    monkeypatch.setattr(auth_module, "get_redis_client", _no_redis)
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
@pytest.mark.parametrize("stamp", ["garbage", "2026-10-12T00:00:00"])
async def test_a_malformed_logout_stamp_fails_closed_without_a_500(
    db: AsyncSession, stamp: str
) -> None:
    app = create_application()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        await _login(c, "198.51.100.210")
        redis_client = await get_redis_client()
        assert redis_client is not None
        await redis_client.set(SESSIONS_VALID_AFTER_KEY, stamp)
        page = await c.get("/dashboard/overview", follow_redirects=False)
        assert page.status_code == 303  # refused, not a 500
        # The logout reads only the cookie, so it still writes a good stamp over the bad one
        out = await c.post("/logout", follow_redirects=False)
        assert out.status_code == 303
        assert out.headers["location"].startswith("/login?message=")
        assert await _login(c, "198.51.100.210") == "/dashboard/overview"
        assert (await c.get("/dashboard/overview")).status_code == 200


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
