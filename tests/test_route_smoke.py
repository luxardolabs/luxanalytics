"""Requests the routes no feature test reaches, so the route-smoke asserter (tests/conftest.py) can
certify every route ran under `filterwarnings = error`. Patterns from `luxarch --emit
route-smoke-example`: log in through the REAL login route; assert the route RAN, not a body shape.
"""

import base64
import hashlib
import hmac
import time

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.models.app_model import App


async def _sign_in(client: AsyncClient, as_client: str) -> None:
    client.base_url = "https://test"  # the session cookie is https_only
    r = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": as_client},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _hmac_headers(body: bytes) -> dict[str, str]:
    key_id, secret = next(iter(settings.hmac_keys_dict.items()))
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), body + timestamp.encode(), hashlib.sha256
    ).hexdigest()
    return {"X-HMAC-Signature": signature, "X-Key-ID": key_id, "X-Timestamp": timestamp}


@pytest.mark.db
async def test_root_redirects_to_the_dashboard(client: AsyncClient) -> None:
    r = await client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/dashboard/overview"


@pytest.mark.db
async def test_logout_clears_the_session(client: AsyncClient) -> None:
    await _sign_in(client, "198.51.100.101")
    r = await client.get("/logout", follow_redirects=False)
    assert r.status_code == 303
    after = await client.get("/dashboard/overview", follow_redirects=False)
    assert after.status_code == 303  # bounced: the session really is gone


@pytest.mark.db
async def test_metrics_scrape_runs(client: AsyncClient) -> None:
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    # The scrape has series: it served an empty private registry nothing registered into.
    assert "python_info" in r.text
    assert "db_pool_checkouts_total" in r.text


@pytest.mark.db
async def test_app_stats_with_hmac(client: AsyncClient) -> None:
    r = await client.get("/api/v1/events/stats", headers=_hmac_headers(b""))
    assert r.status_code < 500


@pytest.mark.db
async def test_legacy_public_dsn_ingest(client: AsyncClient, sample_app: App) -> None:
    token = base64.b64encode(f"{sample_app.public_id}:".encode()).decode()
    r = await client.post(
        "/api/v1/events/public",
        json={"name": "app_opened", "timestamp": "2026-10-06T12:00:00Z"},
        headers={"Authorization": f"Basic {token}"},
    )
    assert r.status_code == 200
    assert r.json()["events_received"] == 1


@pytest.mark.db
async def test_apps_admin_pages_run(client: AsyncClient, sample_app: App) -> None:
    await _sign_in(client, "198.51.100.102")
    for path in ("/apps/", "/apps/new", f"/apps/{sample_app.app_id}/edit"):
        r = await client.get(path)
        assert r.status_code == 200, path


@pytest.mark.db
async def test_deleting_an_app_refreshes_the_list(
    client: AsyncClient, sample_app: App
) -> None:
    await _sign_in(client, "198.51.100.103")
    r = await client.delete(f"/apps/{sample_app.app_id}")
    assert r.status_code == 200
    assert sample_app.app_id not in r.text
