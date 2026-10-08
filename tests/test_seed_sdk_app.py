"""make seed-sdk-app (LUXANALYTI-73): idempotent, dev-only, and its DSN matches the ingest route."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.main import create_application
from app.models.app_model import App
from app.schemas.app_schema import AppUpdate
from app.scripts import seed_sdk_app
from app.services.core.app_core_service import AppCoreService


@pytest.mark.db
async def test_seeding_twice_keeps_one_app_with_stable_ids(db: AsyncSession) -> None:
    first = await seed_sdk_app.ensure_sdk_app(db)
    second = await seed_sdk_app.ensure_sdk_app(db)
    count = await db.execute(
        select(func.count()).select_from(App).where(App.app_id == "sdk-integration")
    )
    assert count.scalar_one() == 1
    assert (first.public_id, first.project_id) == (second.public_id, second.project_id)


@pytest.mark.db
async def test_a_deactivated_app_is_reactivated(db: AsyncSession) -> None:
    await seed_sdk_app.ensure_sdk_app(db)
    await AppCoreService(db).update_app("sdk-integration", AppUpdate(is_active=False))
    assert (await seed_sdk_app.ensure_sdk_app(db)).is_active


@pytest.mark.db
async def test_it_refuses_production(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(seed_sdk_app.NotADevEnvironment):
        await seed_sdk_app.ensure_sdk_app(db)


@pytest.mark.db
async def test_the_dsn_points_at_the_project_ingest_route(db: AsyncSession) -> None:
    app = await seed_sdk_app.ensure_sdk_app(db)
    dsn = seed_sdk_app.dsn_for(app, "https://dev.example.com:4000")
    route = create_application().url_path_for(
        "create_events_by_project_id", project_id=app.project_id
    )
    assert dsn == f"https://{app.public_id}@dev.example.com:4000{route}"
