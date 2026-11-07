"""A duplicate app_id is recognised by the app already being there, not by the name of the unique
index that refused the insert (a database restored from an older schema names it differently)."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_model import App
from app.schemas.app_schema import AppCreate
from app.services.core.app_core_service import AppCoreService


@pytest.mark.db
async def test_a_duplicate_is_recognised_whatever_the_unique_index_is_called(
    dashboard_client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    await db.execute(text("ALTER INDEX ix_apps_app_id RENAME TO apps_app_id_key"))

    response = await dashboard_client.post(
        "/apps/", data={"name": "Dup", "app_id": sample_app.app_id}
    )
    assert response.status_code == 400
    assert "already exists" in response.text


@pytest.mark.db
async def test_a_collision_on_another_unique_column_is_not_a_duplicate_app_id(
    db: AsyncSession, sample_app: App
) -> None:
    core = AppCoreService(db)
    with (
        patch.object(
            AppCoreService, "generate_project_id", return_value=sample_app.project_id
        ),
        pytest.raises(IntegrityError),
    ):
        await core.create_app(AppCreate(app_id="other_id", name="n"))
    # The request transaction is still usable.
    assert await core.get_app_by_app_id("test_app") is not None
