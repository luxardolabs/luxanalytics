"""Stats API (JSON). A JSON router calls the core service; it is never a view's client.

Cross-app data, so it takes the dashboard session (LUXANALYTI-62: it used to answer anyone).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_auth
from app.db.database import get_db
from app.schemas.stats_schema import StatsOverviewResponse
from app.services.core.analytics_core_service import AnalyticsCoreService

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/api/v1/stats/overview", response_model=StatsOverviewResponse)
async def get_overview_stats(
    db: AsyncSession = Depends(get_db),
) -> StatsOverviewResponse:
    overview = await AnalyticsCoreService(db).get_stats_overview(hours=24)
    return StatsOverviewResponse.model_validate(overview)
