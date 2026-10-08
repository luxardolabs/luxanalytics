"""Event ingest (JSON API). HTTP only: authenticate, decode the body, hand it to EventCoreService."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dsn_auth import extract_app_from_dsn, verify_project_dsn
from app.core.security import verify_hmac_signature
from app.db.database import get_db
from app.schemas.event_schema import AppStatsResponse, EventResponse
from app.services.core.event_core_service import EventCoreService, InvalidEventPayload

router = APIRouter()


async def _ingest(request: Request, app_id: str, db: AsyncSession) -> EventResponse:
    """Decode the body and ingest it. A bad body is the caller's error (400/422), never a 500."""
    # LoggingMiddleware stores the (decompressed) body; read it directly when it did not run.
    body = getattr(request.state, "processed_body", None)
    if body is None:
        body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail="Invalid JSON") from e

    try:
        result = await EventCoreService(db).ingest(app_id, payload)
    except InvalidEventPayload as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(
            status_code=422, detail=e.errors(include_url=False, include_input=False)
        ) from e

    return EventResponse(
        status="success",
        events_received=result.received,
        duplicates=result.received - result.stored,
        message=f"Successfully processed {result.received} analytics events",
    )


@router.post("/", response_model=EventResponse)
async def create_events(
    request: Request,
    app_id: str = Depends(verify_hmac_signature),
    db: AsyncSession = Depends(get_db),
) -> EventResponse:
    """
    Create analytics events with HMAC signature authentication.
    Supports both single events and batch events.
    """
    return await _ingest(request, app_id, db)


@router.post("/public", response_model=EventResponse)
async def create_events_public(
    request: Request,
    app_id: str = Depends(extract_app_from_dsn),
    db: AsyncSession = Depends(get_db),
) -> EventResponse:
    """
    Create analytics events with DSN-based authentication.
    Legacy endpoint - kept for compatibility.
    """
    return await _ingest(request, app_id, db)


@router.post("/{project_id}", response_model=EventResponse)
async def create_events_by_project_id(
    request: Request,
    app_id: str = Depends(verify_project_dsn),
    db: AsyncSession = Depends(get_db),
) -> EventResponse:
    """
    Create analytics events using project ID in the URL path (Sentry's DSN pattern):
    PROJECT_ID routes, the PUBLIC_ID in Basic auth authenticates (verify_project_dsn).
    """
    return await _ingest(request, app_id, db)


@router.get("/stats", response_model=AppStatsResponse)
async def get_app_stats(
    app_id: str = Depends(verify_hmac_signature),
    db: AsyncSession = Depends(get_db),
) -> AppStatsResponse:
    """Get basic analytics stats for the app."""
    total_events = await EventCoreService(db).count_events(app_id)
    return AppStatsResponse(app_id=app_id, total_events=total_events, status="active")
