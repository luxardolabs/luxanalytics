import base64
import json
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request  # type: ignore
from sqlalchemy.ext.asyncio import AsyncSession  # type: ignore

from app.core.dsn_auth import extract_app_from_dsn
from app.core.security import verify_hmac_signature
from app.crud.event_crud import event_crud
from app.db.database import get_db
from app.schemas.event_schema import BatchEventRequest, EventCreate, EventResponse
from app.services.app_service import AppService
from app.services.event_service import EventService

logger = logging.getLogger(__name__)
router = APIRouter()


async def _process_events(
    request: Request,
    app_id: str,
    db: AsyncSession,
) -> EventResponse:
    """
    Internal function to process events regardless of authentication method.
    """
    try:
        # Get the processed body (decompressed if it was compressed, raw otherwise)
        processed_body = getattr(request.state, "processed_body", None)
        if processed_body is None:
            # Fallback to reading from request if middleware didn't set it
            processed_body = await request.body()

        payload = json.loads(processed_body)

        logger.debug(
            "Analytics payload received",
            extra={
                "app_id": app_id,
                "payload_size": len(processed_body),
                "payload_type": type(payload).__name__,
            },
        )

        # Handle different payload types
        if isinstance(payload, dict):
            if "events" in payload:
                # Batch format: {"events": [...]}
                batch_request = BatchEventRequest(**payload)
                events = batch_request.events
                logger.debug(
                    "Batch events debug",
                    extra={
                        "app_id": app_id,
                        "batch_size": len(batch_request.events),
                        "event_names": [e.name for e in batch_request.events],
                    },
                )
            else:
                # Single event format: {...}
                events = [EventCreate(**payload)]
                logger.debug(
                    "Single event debug",
                    extra={
                        "app_id": app_id,
                        "event_name": payload.get("name"),
                        "event_timestamp": payload.get("timestamp"),
                        "user_id": payload.get("user_id"),
                        "session_id": payload.get("session_id"),
                        "metadata_keys": list(payload.get("metadata", {}).keys()),
                    },
                )
        elif isinstance(payload, list):
            # Array format: [...]
            events = [EventCreate(**event) for event in payload]
            logger.debug(
                "Array events debug",
                extra={
                    "app_id": app_id,
                    "array_size": len(payload),
                    "event_names": [e.get("name") for e in payload],
                },
            )
        else:
            raise HTTPException(status_code=400, detail="Invalid payload format")

        event_service = EventService(db)
        created_events = await event_service.create_events(app_id, events)

        logger.info(
            "Analytics events processed",
            extra={
                "app_id": app_id,
                "events_count": len(created_events),
                "event_types": list(set(e.name for e in created_events)),
                "batch_type": "batch" if len(events) > 1 else "single",
            },
        )

        return EventResponse(
            status="success",
            events_received=len(created_events),
            message=f"Successfully processed {len(created_events)} analytics events",
        )

    except json.JSONDecodeError:
        logger.error("Invalid JSON payload", extra={"app_id": app_id})
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error(
            "Error processing analytics events",
            exc_info=True,
            extra={"app_id": app_id, "error": str(e)},
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/", response_model=EventResponse)
async def create_events(
    request: Request,
    app_id: str = Depends(verify_hmac_signature),
    db: AsyncSession = Depends(get_db),
):
    """
    Create analytics events with HMAC signature authentication.
    Supports both single events and batch events.
    """
    return await _process_events(request, app_id, db)


@router.post("/public", response_model=EventResponse)
async def create_events_public(
    request: Request,
    app_id: str = Depends(extract_app_from_dsn),
    db: AsyncSession = Depends(get_db),
):
    """
    Create analytics events with DSN-based authentication.
    Legacy endpoint - kept for compatibility.
    """
    return await _process_events(request, app_id, db)


@router.post("/{project_id}", response_model=EventResponse)
async def create_events_by_project_id(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Create analytics events using project ID in the URL path.
    This matches Sentry's DSN pattern exactly.

    DSN format: https://PUBLIC_ID@analytics.domain.com/api/v1/events/PROJECT_ID
    Where:
    - PUBLIC_ID (32 chars) is used for authentication
    - PROJECT_ID (16 digits) is used for routing
    """
    # Look up app by project_id (from URL)
    app_service = AppService(db)
    app = await app_service.get_app_by_project_id(project_id)

    if not app:
        logger.warning("Invalid project_id in URL", extra={"project_id": project_id})
        raise HTTPException(status_code=404, detail="Invalid project")

    # Verify the auth header contains the correct public_id
    authorization = request.headers.get("authorization")
    if authorization and authorization.startswith("Basic "):
        try:
            encoded = authorization.replace("Basic ", "")
            decoded = base64.b64decode(encoded).decode("utf-8")
            auth_public_id = decoded.split(":")[0] if ":" in decoded else decoded

            if not secrets.compare_digest(
                auth_public_id.encode(), app.public_id.encode()
            ):
                logger.warning(
                    "Invalid public_id for project",
                    extra={
                        "project_id": project_id,
                        "expected_public_id": app.public_id[:8] + "...",
                        "provided_public_id": auth_public_id[:8] + "...",
                    },
                )
                raise HTTPException(status_code=401, detail="Invalid DSN key")
        except (ValueError, UnicodeDecodeError) as e:
            # b64decode raises binascii.Error (a ValueError); .decode raises UnicodeDecodeError.
            # A malformed header is the caller's error (401); anything else is a bug and propagates.
            logger.warning("Malformed authorization header", extra={"error": str(e)})
            raise HTTPException(status_code=401, detail="Invalid authorization")
    else:
        logger.warning("Missing authorization header", extra={"project_id": project_id})
        raise HTTPException(status_code=401, detail="Authorization required")

    logger.info(
        "Processing events via project endpoint",
        extra={"app_id": app.app_id, "project_id": project_id},
    )
    return await _process_events(request, app.app_id, db)


@router.get("/stats")
async def get_app_stats(
    app_id: str = Depends(verify_hmac_signature),
    db: AsyncSession = Depends(get_db),
):
    """Get basic analytics stats for the app."""
    conditions = event_crud.time_conditions(hours=8760, app_id=app_id)
    total_events = await event_crud.count(db, conditions)

    return {"app_id": app_id, "total_events": total_events, "status": "active"}
