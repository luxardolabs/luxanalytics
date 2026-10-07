"""The ?v= cache-bust is the build's git SHA, never blank and never a constant sentinel.

fw.static_assets_cache_busted only sees that `?v=` is PRESENT; these tests check the token itself
(luxarch --playbook cache-busting: an empty or constant token passes the guard and busts nothing).
"""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.web import template_filters


@pytest.mark.parametrize("unstamped", [None, "", "unknown", "dev", "local"])
def test_an_unstamped_build_gets_the_dev_token(
    monkeypatch: pytest.MonkeyPatch, unstamped: str | None
) -> None:
    monkeypatch.setattr(settings, "BUILD_COMMIT", unstamped)
    assert template_filters.static_version() == "dev"


def test_a_stamped_build_busts_with_its_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BUILD_COMMIT", "0a8bf9d")
    assert template_filters.static_version() == "0a8bf9d"


@pytest.mark.db
async def test_the_rendered_page_carries_a_real_token(client: AsyncClient) -> None:
    response = await client.get("/login")
    assert 'href="/static/css/compiled.css?v=' in response.text
    assert '?v="' not in response.text  # never an empty token
