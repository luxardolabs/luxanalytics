"""A NUL byte in a dashboard filter or path value is refused where the request is parsed (422);
the database cannot store one, so it never reaches a query."""

import pytest
from httpx import AsyncClient

NUL_URLS = [
    "/dashboard/overview?app_id=%00",
    "/dashboard/events?app_id=a%00b",
    "/dashboard/devices?app_id=%00",
    "/dashboard/overview/content?app_id=a%00b",
    "/dashboard/events?event_name=a%00b",
    "/dashboard/events/content?event_name=a%00b",
    "/dashboard/events/content?search_q=a%00b",
    "/dashboard/explorer/content?event_name=a%00b",
    "/dashboard/explorer?event_name=a%00b",
    "/dashboard/user/a%00b",
    "/dashboard/session/a%00b",
    "/dashboard/explorer/key/a%00b",
    "/dashboard/explorer/key/a%00b?app_id=x",
]


@pytest.mark.db
@pytest.mark.parametrize("url", NUL_URLS)
async def test_a_nul_byte_is_a_422(dashboard_client: AsyncClient, url: str) -> None:
    response = await dashboard_client.get(url)
    assert response.status_code == 422


@pytest.mark.db
@pytest.mark.parametrize(
    "url",
    [
        "/dashboard/overview?app_id=x",
        "/dashboard/events/content?app_id=&event_name=&search_q=",
        "/dashboard/events/content?event_name=ab&search_q=ab",
        "/dashboard/explorer/content?event_name=ab",
        "/dashboard/user/ab",
        "/dashboard/session/ab",
        "/dashboard/explorer/key/ab",
        "/dashboard/user/a%2Fb",
    ],
)
async def test_the_same_values_without_a_nul_byte_render(
    dashboard_client: AsyncClient, url: str
) -> None:
    response = await dashboard_client.get(url)
    assert response.status_code == 200
