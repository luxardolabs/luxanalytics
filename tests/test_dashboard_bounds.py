"""The dashboard's hours and page filters are bounded in the route signature: a value no query can
use is a 422, not a database or date-arithmetic error."""

import pytest
from httpx import AsyncClient

from app.core.query_params import MAX_HOURS, MAX_PAGE

HOURS_PAGES = [
    "/dashboard/overview",
    "/dashboard/overview/content",
    "/dashboard/overview/timeline",
    "/dashboard/events",
    "/dashboard/events/content",
    "/dashboard/devices/content",
    "/dashboard/errors/content",
    "/dashboard/performance/content",
    "/dashboard/features/content",
    "/dashboard/journey/content",
    "/dashboard/feedback/content",
    "/dashboard/explorer/content",
]


@pytest.mark.db
@pytest.mark.parametrize("path", HOURS_PAGES)
@pytest.mark.parametrize("hours", ["100000000", "9999999999", "-1", "abc"])
async def test_an_unusable_hours_value_is_a_422(
    dashboard_client: AsyncClient, path: str, hours: str
) -> None:
    response = await dashboard_client.get(f"{path}?hours={hours}")
    assert response.status_code == 422


@pytest.mark.db
@pytest.mark.parametrize("path", HOURS_PAGES)
@pytest.mark.parametrize("hours", ["0", "24", str(MAX_HOURS)])
async def test_the_usable_hours_values_render(
    dashboard_client: AsyncClient, path: str, hours: str
) -> None:
    response = await dashboard_client.get(f"{path}?hours={hours}")
    assert response.status_code == 200


@pytest.mark.db
@pytest.mark.parametrize("path", ["/dashboard/events", "/dashboard/events/content"])
@pytest.mark.parametrize("page", ["10000000000000000000", str(MAX_PAGE + 1), "0", "-3"])
async def test_an_unusable_page_value_is_a_422(
    dashboard_client: AsyncClient, path: str, page: str
) -> None:
    response = await dashboard_client.get(f"{path}?page={page}")
    assert response.status_code == 422


@pytest.mark.db
@pytest.mark.parametrize("path", ["/dashboard/events", "/dashboard/events/content"])
@pytest.mark.parametrize("page", ["1", "99999", str(MAX_PAGE)])
async def test_the_usable_page_values_render(
    dashboard_client: AsyncClient, path: str, page: str
) -> None:
    response = await dashboard_client.get(f"{path}?page={page}")
    assert response.status_code == 200
