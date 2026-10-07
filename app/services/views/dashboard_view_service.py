"""DashboardViewService — facade for dashboard routes.

Delegates to AnalyticsCoreService (core business logic).
View services are thin wrappers that format data for templates.
Architecture: Router → DashboardViewService → AnalyticsCoreService → CRUD → DB
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core.analytics_core_service import AnalyticsCoreService


class DashboardViewService:
    """Thin facade — routes call this, it calls the core analytics service."""

    def __init__(self, db: AsyncSession):
        self._svc = AnalyticsCoreService(db)

    async def get_apps_with_stats(self) -> list[dict[str, Any]]:
        return await self._svc.get_apps_with_stats()

    async def get_apps_for_dropdown(self) -> list[dict[str, Any]]:
        return await self._svc.get_apps_for_dropdown()

    async def get_filtered_events(self, **kwargs) -> dict:
        return await self._svc.get_filtered_events(**kwargs)

    async def get_event_by_id(self, event_id: str):
        return await self._svc.get_event_by_id(event_id)

    async def get_stats_overview(self, **kwargs) -> dict[str, Any]:
        return await self._svc.get_stats_overview(**kwargs)

    async def get_timeline_data(self, **kwargs) -> list[dict[str, Any]]:
        return await self._svc.get_timeline_data(**kwargs)

    async def search_events(self, **kwargs) -> list:
        return await self._svc.search_events(**kwargs)

    async def get_device_analytics(self, **kwargs) -> dict[str, Any]:
        return await self._svc.get_device_analytics(**kwargs)

    async def get_feature_analytics(self, **kwargs) -> dict[str, Any]:
        return await self._svc.get_feature_analytics(**kwargs)

    async def get_error_analytics(self, **kwargs) -> dict[str, Any]:
        return await self._svc.get_error_analytics(**kwargs)

    async def get_performance_analytics(self, **kwargs) -> dict[str, Any]:
        return await self._svc.get_performance_analytics(**kwargs)

    async def get_user_journey_analytics(self, **kwargs) -> dict[str, Any]:
        return await self._svc.get_user_journey_analytics(**kwargs)

    async def get_feedback_analytics(self, **kwargs) -> dict[str, Any]:
        return await self._svc.get_feedback_analytics(**kwargs)

    async def get_metadata_analysis(self, **kwargs) -> dict[str, Any]:
        return await self._svc.get_metadata_analysis(**kwargs)

    async def get_key_deep_dive(self, key_name: str, **kwargs) -> dict[str, Any]:
        return await self._svc.get_key_deep_dive(key_name, **kwargs)

    async def get_user_profile(self, user_id: str) -> dict[str, Any]:
        return await self._svc.get_user_profile(user_id)

    async def get_session_detail(self, session_id: str) -> dict[str, Any]:
        return await self._svc.get_session_detail(session_id)
