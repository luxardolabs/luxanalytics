from fastapi import APIRouter

from app.web.routers.apps_router import router as apps_router
from app.web.routers.auth_router import router as auth_router
from app.web.routers.dashboard_router import router as dashboard_router

router = APIRouter()
router.include_router(auth_router, tags=["auth"])
router.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
router.include_router(apps_router, prefix="/apps", tags=["apps"])
