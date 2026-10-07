from fastapi import APIRouter

from app.api.v1.routers.events_router import router as events_router
from app.api.v1.routers.stats_router import router as stats_router

router = APIRouter()
router.include_router(events_router, prefix="/api/v1/events", tags=["events"])
router.include_router(stats_router, tags=["stats"])
