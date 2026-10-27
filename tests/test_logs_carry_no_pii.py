"""Request logs carry no end-user identifiers and no typed credentials.

The dashboard's URLs hold SDK end-user ids (`/dashboard/user/{user_id}`, `/dashboard/session/{id}`,
`?search_q=`), so a request is logged by its path template, and uvicorn's access line is off. A
failed login logs nothing that was typed, where a password typed into the wrong field would land.
"""

import logging

import pytest
from httpx import AsyncClient

from app.main import app as served_app
from tests.test_auth_session import _login

END_USER = "alice.smith@example.com"


def _logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    """What the app and its server logged (not the test's own httpx client)."""
    return [
        str(vars(record))
        for record in caplog.records
        if record.name.startswith(("app.", "uvicorn"))
    ]


@pytest.mark.db
@pytest.mark.parametrize(
    "path",
    [
        f"/dashboard/user/{END_USER}",
        f"/dashboard/session/{END_USER}",
        f"/dashboard/events/content?search_q={END_USER}",
    ],
)
async def test_a_request_log_names_the_route_not_the_end_user(
    client: AsyncClient, caplog: pytest.LogCaptureFixture, path: str
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = await client.get(path, follow_redirects=False)

    assert r.status_code == 303  # unauthenticated: bounced, and still logged
    logged = _logged(caplog)
    assert any("Request completed" in line for line in logged)
    assert not [line for line in logged if END_USER in line]


@pytest.mark.db
async def test_a_signed_in_request_is_logged_by_its_route_template(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    await _login(client, "198.51.100.101")
    with caplog.at_level(logging.INFO):
        await client.get(f"/dashboard/user/{END_USER}")

    completed = [r for r in caplog.records if r.getMessage() == "Request completed"]
    assert completed
    assert vars(completed[-1])["route"] == "/dashboard/user/{user_id}"


def test_uvicorns_access_log_is_off() -> None:
    """It logs every path and query string; the middleware's line, by route, replaces it. The
    served app's module configured logging when it was imported."""
    assert served_app.title
    assert not logging.getLogger("uvicorn.access").isEnabledFor(logging.INFO)


@pytest.mark.db
async def test_a_failed_login_does_not_log_what_was_typed(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    typed = "Correct-Horse-Battery-Staple-typed-in-the-user-field"
    client.base_url = "https://test"
    with caplog.at_level(logging.DEBUG):
        r = await client.post(
            "/login",
            data={"username": typed, "password": "x"},
            headers={"X-Forwarded-For": "198.51.100.102"},
            follow_redirects=False,
        )

    assert r.status_code == 303
    logged = _logged(caplog)
    assert any("Failed login attempt" in line for line in logged)
    assert not any(typed in line for line in logged)
