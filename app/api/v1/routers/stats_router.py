"""Stats API router — calls DashboardViewService facade, never services directly."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.services.views.dashboard_view_service import DashboardViewService

router = APIRouter()


@router.get("/api/v1/stats/overview")
async def get_overview_stats(db: AsyncSession = Depends(get_db)):
    svc = DashboardViewService(db)
    return await svc.get_stats_overview(hours=24)
