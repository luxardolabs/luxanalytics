"""Liveness and metrics (JSON / scrape). Calls core; never the database directly."""

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.core.telemetry import get_prometheus_metrics
from app.schemas.health_schema import HealthResponse
from app.services.core.health_core_service import HealthCoreService

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint with database pool stats."""
    return await HealthCoreService().report()


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=get_prometheus_metrics(), media_type=CONTENT_TYPE_LATEST)
