"""GET /api/v1/stats/overview: event totals across apps (dashboard session only)."""

from pydantic import BaseModel


class TopEvent(BaseModel):
    name: str
    count: int


class StatsOverviewResponse(BaseModel):
    """The last `hours` of events: total, distinct users, and the most frequent event names."""

    total_events: int
    unique_users: int
    top_events: list[TopEvent]
    hours: int
