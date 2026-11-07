"""A refused (422) request is answered for the client that made it: the web routes answer HTML
that names the refused parameter and never echoes the value; /api keeps its JSON."""

import base64
import json

import pytest
from httpx import AsyncClient

HX = {"HX-Request": "true"}
HOSTILE = "<script>alert(1)</script>"


@pytest.mark.db
async def test_a_refused_htmx_partial_is_an_error_partial_the_client_swaps(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.get(
        "/dashboard/events/content", params={"hours": HOSTILE}, headers=HX
    )
    assert response.status_code == 422
    assert response.headers["hx-error-swap"] == "true"
    assert "text/html" in response.headers["content-type"]
    assert "hours" in response.text
    assert HOSTILE not in response.text
    assert "alert(1)" not in response.text


@pytest.mark.db
async def test_a_refused_full_page_is_an_error_page_that_does_not_echo_the_input(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.get(
        "/dashboard/events", params={"page": HOSTILE, "hours": "99999999"}
    )
    assert response.status_code == 422
    assert "text/html" in response.headers["content-type"]
    assert "hx-error-swap" not in response.headers
    assert "<html" in response.text
    assert "page" in response.text and "hours" in response.text
    assert "alert(1)" not in response.text
    assert "99999999" not in response.text


@pytest.mark.db
async def test_a_refused_path_value_is_html_too(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.get("/dashboard/session/a%00b", headers=HX)
    assert response.status_code == 422
    assert response.headers["hx-error-swap"] == "true"
    assert "session_id" in response.text


@pytest.mark.db
async def test_a_refused_app_selection_toasts_and_does_not_swap_the_select(
    dashboard_client: AsyncClient,
) -> None:
    # The nav select posts with hx-swap="none", but htmx-error-swap.js writes an error body into
    # the request's target whenever HX-Error-Swap is set: that would replace the select's options.
    response = await dashboard_client.post(
        "/set-app-context",
        data={"app_id": "a\x00b"},
        headers={**HX, "HX-Target": "nav-app-select"},
    )
    assert response.status_code == 422
    assert "hx-error-swap" not in response.headers
    toast = json.loads(response.headers["hx-trigger"])["showtoast"]
    assert toast["type"] == "error"
    assert toast["message"]
    assert "a\x00b" not in response.headers["hx-trigger"]


@pytest.mark.db
async def test_the_api_keeps_its_json(client: AsyncClient, sample_app: object) -> None:
    token = base64.b64encode(b"test_public_id_12345678901:").decode()
    response = await client.post(
        "/api/v1/events/1234567890123456",
        json={"timestamp": "2026-10-06T12:00:00Z"},
        headers={"Authorization": f"Basic {token}", "HX-Request": "true"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert "hx-error-swap" not in response.headers
    assert isinstance(response.json()["detail"], list)
