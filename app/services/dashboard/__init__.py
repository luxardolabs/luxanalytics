"""Dashboard service package.

Re-exports DashboardService with the same API as the old monolithic module
so the router import `from app.services.dashboard import DashboardService` keeps working.
"""

from app.services.dashboard.service import DashboardService

__all__ = ["DashboardService"]
